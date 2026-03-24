"""Dataset service: CRUD operations for persistent datasets and rows."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.core.database import set_tenant_context
from server.data.dataset_models import Dataset, DatasetRow


def _slugify(name: str) -> str:
    """Convert a human-readable name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug or "dataset"


def _compute_dedup_hash(data: dict[str, Any], dedup_key: str) -> str | None:
    """Compute a dedup hash from the specified key in the row data."""
    val = data.get(dedup_key)
    if val is None:
        return None
    return hashlib.sha256(str(val).encode()).hexdigest()[:32]


async def create_dataset(
    db: AsyncSession,
    tenant_id: str,
    name: str,
    *,
    description: str | None = None,
    columns: list[dict[str, str]] | None = None,
    dedup_key: str | None = None,
    workflow_id: str | None = None,
) -> dict[str, Any]:
    """Create a new dataset. Returns dataset metadata."""
    await set_tenant_context(db, tenant_id)

    slug = _slugify(name)

    # Check for existing dataset with same slug
    existing = await db.execute(
        select(Dataset).where(
            Dataset.tenant_id == tenant_id,
            Dataset.slug == slug,
        )
    )
    existing_ds = existing.scalar_one_or_none()
    if existing_ds:
        # Return existing dataset instead of erroring
        return {
            "id": str(existing_ds.id),
            "name": existing_ds.name,
            "slug": existing_ds.slug,
            "description": existing_ds.description,
            "columns": existing_ds.columns,
            "dedup_key": existing_ds.dedup_key,
            "row_count": existing_ds.row_count,
            "status": "exists",
            "message": f"Dataset '{slug}' already exists. Use it directly.",
        }

    ds = Dataset(
        tenant_id=tenant_id,
        name=name,
        slug=slug,
        description=description,
        columns=columns or [],
        dedup_key=dedup_key,
        created_by_workflow=workflow_id,
    )
    db.add(ds)
    await db.commit()
    await db.refresh(ds)

    return {
        "id": str(ds.id),
        "name": ds.name,
        "slug": ds.slug,
        "description": ds.description,
        "columns": ds.columns,
        "dedup_key": ds.dedup_key,
        "row_count": 0,
        "status": "created",
    }


async def list_datasets(
    db: AsyncSession,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """List all active datasets for a tenant."""
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(Dataset)
        .where(Dataset.tenant_id == tenant_id, Dataset.status == "active")
        .order_by(Dataset.updated_at.desc())
    )
    datasets = result.scalars().all()

    return [
        {
            "id": str(ds.id),
            "name": ds.name,
            "slug": ds.slug,
            "description": ds.description,
            "columns": ds.columns,
            "dedup_key": ds.dedup_key,
            "row_count": ds.row_count,
            "created_at": ds.created_at.isoformat() if ds.created_at else None,
            "updated_at": ds.updated_at.isoformat() if ds.updated_at else None,
        }
        for ds in datasets
    ]


async def get_dataset(
    db: AsyncSession,
    tenant_id: str,
    dataset_id: str | None = None,
    slug: str | None = None,
) -> Dataset | None:
    """Get a dataset by ID or slug."""
    await set_tenant_context(db, tenant_id)

    if dataset_id:
        result = await db.execute(
            select(Dataset).where(
                Dataset.tenant_id == tenant_id,
                Dataset.id == uuid.UUID(dataset_id),
            )
        )
    elif slug:
        result = await db.execute(
            select(Dataset).where(
                Dataset.tenant_id == tenant_id,
                Dataset.slug == slug,
            )
        )
    else:
        return None

    return result.scalar_one_or_none()


async def append_rows(
    db: AsyncSession,
    tenant_id: str,
    dataset_id: str,
    rows: list[dict[str, Any]],
    *,
    workflow_id: str | None = None,
) -> dict[str, Any]:
    """Append rows to a dataset. Supports dedup via the dataset's dedup_key.

    Returns counts of inserted, updated, and skipped rows.
    """
    await set_tenant_context(db, tenant_id)

    # Get dataset metadata
    ds = await db.execute(
        select(Dataset).where(
            Dataset.tenant_id == tenant_id,
            Dataset.id == uuid.UUID(dataset_id),
        )
    )
    dataset = ds.scalar_one_or_none()
    if not dataset:
        return {"error": f"Dataset {dataset_id} not found."}

    inserted = 0
    updated = 0
    skipped = 0

    for row_data in rows:
        dedup_hash = None
        if dataset.dedup_key:
            dedup_hash = _compute_dedup_hash(row_data, dataset.dedup_key)

            if dedup_hash:
                # Check for existing row with same dedup hash
                existing = await db.execute(
                    select(DatasetRow).where(
                        DatasetRow.dataset_id == dataset.id,
                        DatasetRow.dedup_hash == dedup_hash,
                    )
                )
                existing_row = existing.scalar_one_or_none()

                if existing_row:
                    # Update existing row (merge new data)
                    merged = {**existing_row.data, **row_data}
                    await db.execute(
                        update(DatasetRow)
                        .where(DatasetRow.id == existing_row.id)
                        .values(
                            data=merged,
                            workflow_id=workflow_id,
                            updated_at=func.now(),
                        )
                    )
                    updated += 1
                    continue

        # Insert new row
        new_row = DatasetRow(
            tenant_id=tenant_id,
            dataset_id=dataset.id,
            data=row_data,
            dedup_hash=dedup_hash,
            workflow_id=workflow_id,
        )
        db.add(new_row)
        inserted += 1

    # Flush so the count query sees newly added rows
    await db.flush()

    # Update row count from actual DB count
    count_result = await db.execute(
        select(func.count()).where(DatasetRow.dataset_id == dataset.id)
    )
    total_rows = count_result.scalar_one()
    await db.execute(
        update(Dataset)
        .where(Dataset.id == dataset.id)
        .values(row_count=total_rows, updated_at=func.now())
    )

    await db.commit()

    return {
        "dataset_id": dataset_id,
        "dataset_slug": dataset.slug,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "total_rows": total_rows,
    }


async def query_rows(
    db: AsyncSession,
    tenant_id: str,
    dataset_id: str | None = None,
    slug: str | None = None,
    *,
    filters: dict[str, Any] | None = None,
    order_by: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Query rows from a dataset with optional filters and pagination."""
    await set_tenant_context(db, tenant_id)

    # Resolve dataset
    dataset = await get_dataset(db, tenant_id, dataset_id=dataset_id, slug=slug)
    if not dataset:
        return {"error": "Dataset not found."}

    # Build query
    base = select(DatasetRow).where(DatasetRow.dataset_id == dataset.id)

    # Apply JSONB filters
    if filters:
        for key, val in filters.items():
            if not key.isidentifier():
                continue
            base = base.where(
                DatasetRow.data[key].as_string() == str(val)
            )

    # Ordering
    if order_by:
        col = order_by.lstrip("-")
        if col == "created_at":
            if order_by.startswith("-"):
                base = base.order_by(DatasetRow.created_at.desc())
            else:
                base = base.order_by(DatasetRow.created_at.asc())
        else:
            # Order by JSONB field
            if order_by.startswith("-"):
                base = base.order_by(DatasetRow.data[col].desc())
            else:
                base = base.order_by(DatasetRow.data[col].asc())
    else:
        base = base.order_by(DatasetRow.created_at.desc())

    # Count
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar_one()

    # Paginate
    result = await db.execute(base.limit(limit).offset(offset))
    rows = result.scalars().all()

    return {
        "dataset": {
            "id": str(dataset.id),
            "name": dataset.name,
            "slug": dataset.slug,
            "columns": dataset.columns,
        },
        "rows": [
            {
                "id": str(r.id),
                **r.data,
                "_created_at": r.created_at.isoformat() if r.created_at else None,
                "_workflow_id": r.workflow_id,
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def delete_rows(
    db: AsyncSession,
    tenant_id: str,
    dataset_id: str,
    row_ids: list[str] | None = None,
    *,
    all_rows: bool = False,
) -> dict[str, Any]:
    """Delete specific rows or all rows from a dataset."""
    await set_tenant_context(db, tenant_id)

    ds_uuid = uuid.UUID(dataset_id)

    if all_rows:
        result = await db.execute(
            delete(DatasetRow).where(
                DatasetRow.dataset_id == ds_uuid,
                DatasetRow.tenant_id == tenant_id,
            )
        )
        deleted = result.rowcount
    elif row_ids:
        uuids = [uuid.UUID(rid) for rid in row_ids]
        result = await db.execute(
            delete(DatasetRow).where(
                DatasetRow.dataset_id == ds_uuid,
                DatasetRow.tenant_id == tenant_id,
                DatasetRow.id.in_(uuids),
            )
        )
        deleted = result.rowcount
    else:
        return {"error": "Provide row_ids or set all_rows=true."}

    # Update count
    count_result = await db.execute(
        select(func.count()).where(DatasetRow.dataset_id == ds_uuid)
    )
    new_count = count_result.scalar_one()
    await db.execute(
        update(Dataset)
        .where(Dataset.id == ds_uuid)
        .values(row_count=new_count, updated_at=func.now())
    )

    await db.commit()

    return {"deleted": deleted, "remaining_rows": new_count}


async def update_dataset(
    db: AsyncSession,
    tenant_id: str,
    dataset_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    columns: list[dict[str, str]] | None = None,
    dedup_key: str | None = None,
) -> dict[str, Any]:
    """Update dataset metadata. Only provided fields are changed."""
    await set_tenant_context(db, tenant_id)

    ds_uuid = uuid.UUID(dataset_id)
    result = await db.execute(
        select(Dataset).where(
            Dataset.tenant_id == tenant_id,
            Dataset.id == ds_uuid,
        )
    )
    dataset = result.scalar_one_or_none()
    if not dataset:
        return {"error": f"Dataset {dataset_id} not found."}

    changes: dict[str, Any] = {"updated_at": func.now()}
    if name is not None:
        new_slug = _slugify(name)
        # Check slug collision
        existing = await db.execute(
            select(Dataset).where(
                Dataset.tenant_id == tenant_id,
                Dataset.slug == new_slug,
                Dataset.id != ds_uuid,
            )
        )
        if existing.scalar_one_or_none():
            return {"error": f"A dataset with slug '{new_slug}' already exists."}
        changes["name"] = name
        changes["slug"] = new_slug
    if description is not None:
        changes["description"] = description
    if columns is not None:
        changes["columns"] = columns
    if dedup_key is not None:
        changes["dedup_key"] = dedup_key

    await db.execute(
        update(Dataset).where(Dataset.id == ds_uuid).values(**changes)
    )
    await db.commit()

    # Refresh and return
    refreshed = await db.execute(
        select(Dataset).where(Dataset.id == ds_uuid)
    )
    ds = refreshed.scalar_one()
    return {
        "id": str(ds.id),
        "name": ds.name,
        "slug": ds.slug,
        "description": ds.description,
        "columns": ds.columns,
        "dedup_key": ds.dedup_key,
        "row_count": ds.row_count,
        "status": "updated",
    }


async def delete_dataset(
    db: AsyncSession,
    tenant_id: str,
    dataset_id: str,
) -> dict[str, Any]:
    """Soft-delete a dataset by setting status to 'archived'."""
    await set_tenant_context(db, tenant_id)

    ds_uuid = uuid.UUID(dataset_id)
    result = await db.execute(
        select(Dataset).where(
            Dataset.tenant_id == tenant_id,
            Dataset.id == ds_uuid,
        )
    )
    dataset = result.scalar_one_or_none()
    if not dataset:
        return {"error": f"Dataset {dataset_id} not found."}

    await db.execute(
        update(Dataset)
        .where(Dataset.id == ds_uuid)
        .values(status="archived", updated_at=func.now())
    )
    await db.commit()

    return {
        "id": str(dataset.id),
        "name": dataset.name,
        "slug": dataset.slug,
        "status": "archived",
        "message": f"Dataset '{dataset.name}' has been archived.",
    }
