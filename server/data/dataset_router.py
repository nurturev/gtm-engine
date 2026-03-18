"""Dataset router: CRUD API for persistent datasets and their rows."""

from __future__ import annotations

from typing import Any, Optional

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.models import Tenant
from server.core.config import settings
from server.core.database import get_db, set_tenant_context
from server.data import dataset_service as svc

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])

_COOKIE_NAME = "nrv_session"


# ---------------------------------------------------------------------------
# Flexible auth — accepts Bearer, cookie, or query param
# ---------------------------------------------------------------------------


async def _get_tenant_flexible(
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> tuple[Tenant, AsyncSession]:
    """Authenticate via Bearer token, session cookie, or query param.

    Supports both MCP client (Bearer) and dashboard (cookie) callers.
    """
    jwt_token: str | None = None

    # 1. Authorization header (MCP client / API)
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        jwt_token = auth_header.removeprefix("Bearer ")

    # 2. Cookie (dashboard browser sessions)
    if not jwt_token:
        jwt_token = request.cookies.get(_COOKIE_NAME)

    # 3. Query param (legacy)
    if not jwt_token and token:
        jwt_token = token

    if not jwt_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    try:
        payload = jwt.decode(
            jwt_token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please sign in again.",
        )

    tenant_id: str | None = payload.get("tenant_id")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing tenant_id",
        )

    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    await set_tenant_context(db, tenant.id)
    return tenant, db


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateDatasetRequest(BaseModel):
    name: str
    description: str | None = None
    columns: list[dict[str, str]] | None = None
    dedup_key: str | None = None
    workflow_id: str | None = None


class AppendRowsRequest(BaseModel):
    rows: list[dict[str, Any]]
    workflow_id: str | None = None


class DeleteRowsRequest(BaseModel):
    row_ids: list[str] | None = None
    all_rows: bool = False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("")
async def create_dataset(
    request: Request,
    body: CreateDatasetRequest,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a new persistent dataset (or return existing if slug matches)."""
    tenant, db = await _get_tenant_flexible(request, token=token, db=db)
    result = await svc.create_dataset(
        db,
        tenant.id,
        body.name,
        description=body.description,
        columns=body.columns,
        dedup_key=body.dedup_key,
        workflow_id=body.workflow_id,
    )
    return result


@router.get("")
async def list_datasets(
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List all active datasets for the current tenant."""
    tenant, db = await _get_tenant_flexible(request, token=token, db=db)
    datasets = await svc.list_datasets(db, tenant.id)
    return {"datasets": datasets, "count": len(datasets)}


@router.get("/{dataset_ref}")
async def get_dataset(
    request: Request,
    dataset_ref: str,
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    order_by: str | None = None,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get dataset metadata and rows. dataset_ref can be UUID or slug."""
    tenant, db = await _get_tenant_flexible(request, token=token, db=db)
    # Determine if ref is a UUID or slug
    is_uuid = len(dataset_ref) == 36 and "-" in dataset_ref
    result = await svc.query_rows(
        db,
        tenant.id,
        dataset_id=dataset_ref if is_uuid else None,
        slug=dataset_ref if not is_uuid else None,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])
    return result


@router.post("/{dataset_ref}/rows")
async def append_rows(
    request: Request,
    dataset_ref: str,
    body: AppendRowsRequest,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Append rows to a dataset. Supports upsert via the dataset's dedup_key."""
    tenant, db = await _get_tenant_flexible(request, token=token, db=db)
    # Resolve to dataset_id
    is_uuid = len(dataset_ref) == 36 and "-" in dataset_ref
    ds = await svc.get_dataset(
        db, tenant.id,
        dataset_id=dataset_ref if is_uuid else None,
        slug=dataset_ref if not is_uuid else None,
    )
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset '{dataset_ref}' not found.",
        )

    result = await svc.append_rows(
        db,
        tenant.id,
        str(ds.id),
        body.rows,
        workflow_id=body.workflow_id,
    )
    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["error"],
        )
    return result


@router.delete("/{dataset_ref}/rows")
async def delete_rows(
    request: Request,
    dataset_ref: str,
    body: DeleteRowsRequest,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Delete specific rows or all rows from a dataset."""
    tenant, db = await _get_tenant_flexible(request, token=token, db=db)
    is_uuid = len(dataset_ref) == 36 and "-" in dataset_ref
    ds = await svc.get_dataset(
        db, tenant.id,
        dataset_id=dataset_ref if is_uuid else None,
        slug=dataset_ref if not is_uuid else None,
    )
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset '{dataset_ref}' not found.",
        )

    result = await svc.delete_rows(
        db,
        tenant.id,
        str(ds.id),
        row_ids=body.row_ids,
        all_rows=body.all_rows,
    )
    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["error"],
        )
    return result


# ------------------------------------------------------------------
# Column metadata
# ------------------------------------------------------------------

@router.get("/{dataset_ref}/metadata")
async def get_dataset_metadata(
    dataset_ref: str,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get column metadata for a dataset (type, null%, unique count, etc.)."""
    from server.execution.column_metadata import compute_column_metadata

    tenant, db = await _get_tenant_flexible(request, token=token, db=db)
    is_uuid = len(dataset_ref) == 36 and "-" in dataset_ref

    # Resolve dataset
    from server.data.dataset_models import Dataset
    if is_uuid:
        q = select(Dataset).where(Dataset.id == dataset_ref, Dataset.tenant_id == tenant.id)
    else:
        q = select(Dataset).where(Dataset.slug == dataset_ref, Dataset.tenant_id == tenant.id)
    ds = (await db.execute(q)).scalar_one_or_none()
    if not ds:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_ref}' not found.")

    # Query up to 1000 rows for profiling
    rows_result = await svc.query_rows(
        db, tenant.id,
        dataset_id=str(ds.id) if is_uuid else None,
        slug=dataset_ref if not is_uuid else None,
        limit=1000,
    )
    raw_rows = rows_result.get("rows", [])

    # Extract just the data dicts
    row_dicts = [r.get("data", r) if isinstance(r, dict) else r for r in raw_rows]
    metadata = compute_column_metadata(row_dicts)

    return {
        "dataset": ds.name,
        "slug": ds.slug,
        "row_count": ds.row_count,
        "columns": metadata,
    }


# ------------------------------------------------------------------
# CSV download
# ------------------------------------------------------------------

@router.get("/{dataset_ref}/csv")
async def download_dataset_csv(
    dataset_ref: str,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Download all dataset rows as CSV (max 10,000 rows)."""
    tenant, db = await _get_tenant_flexible(request, token=token, db=db)
    is_uuid = len(dataset_ref) == 36 and "-" in dataset_ref

    from server.data.dataset_models import Dataset
    if is_uuid:
        q = select(Dataset).where(Dataset.id == dataset_ref, Dataset.tenant_id == tenant.id)
    else:
        q = select(Dataset).where(Dataset.slug == dataset_ref, Dataset.tenant_id == tenant.id)
    ds = (await db.execute(q)).scalar_one_or_none()
    if not ds:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_ref}' not found.")

    rows_result = await svc.query_rows(
        db, tenant.id,
        dataset_id=str(ds.id) if is_uuid else None,
        slug=dataset_ref if not is_uuid else None,
        limit=10000,
    )
    raw_rows = rows_result.get("rows", [])

    # Flatten data dicts
    flat_rows = [r.get("data", r) if isinstance(r, dict) else r for r in raw_rows]

    if not flat_rows:
        output = io.StringIO()
        output.write("No rows in this dataset\n")
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={ds.slug}.csv"},
        )

    # Union all column names
    all_cols: list[str] = []
    seen: set[str] = set()
    skip = {"id", "_created_at", "_workflow_id", "_updated_at", "dedup_hash"}
    for row in flat_rows:
        if isinstance(row, dict):
            for k in row.keys():
                if k not in seen and k not in skip:
                    all_cols.append(k)
                    seen.add(k)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=all_cols, extrasaction="ignore")
    writer.writeheader()
    for row in flat_rows:
        if isinstance(row, dict):
            writer.writerow({k: v for k, v in row.items() if k not in skip})

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={ds.slug}.csv"},
    )
