"""Dataset router: CRUD API for persistent datasets and their rows."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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
