"""Configurable operation credit costs — DB-backed with in-memory cache.

The cache is loaded at startup and refreshed after every admin write.
`get_base_cost()` is the single entry point used by `calculate_cost()`
in the execution layer.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.billing.models import OperationCost

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_vendor_op_cache: dict[str, float] = {}   # "vendor:operation" -> cost
_op_cache: dict[str, float] = {}          # "operation" -> cost (first vendor wins)
_cache_loaded: bool = False


async def load_cost_cache(db: AsyncSession) -> None:
    """Load all operation costs from the DB into memory."""
    global _vendor_op_cache, _op_cache, _cache_loaded

    result = await db.execute(select(OperationCost))
    rows = result.scalars().all()

    new_vendor_op: dict[str, float] = {}
    new_op: dict[str, float] = {}

    for row in rows:
        new_vendor_op[f"{row.vendor}:{row.operation}"] = float(row.base_cost)
        # Operation-level lookup: keep the first vendor's cost as default
        if row.operation not in new_op:
            new_op[row.operation] = float(row.base_cost)

    _vendor_op_cache = new_vendor_op
    _op_cache = new_op
    _cache_loaded = True
    logger.info("Loaded %d operation cost entries into cache", len(rows))


def get_base_cost(operation: str, vendor: str | None = None) -> float:
    """Return the base credit cost for an operation.

    Lookup order:
    1. vendor:operation (if vendor provided)
    2. operation (first vendor that defined it)
    3. 1.0 fallback
    """
    if vendor:
        key = f"{vendor}:{operation}"
        if key in _vendor_op_cache:
            return _vendor_op_cache[key]
    if operation in _op_cache:
        return _op_cache[operation]
    return 1.0


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def list_costs(db: AsyncSession) -> list[OperationCost]:
    result = await db.execute(
        select(OperationCost).order_by(OperationCost.vendor, OperationCost.operation)
    )
    return list(result.scalars().all())


async def get_cost(db: AsyncSession, cost_id: int) -> OperationCost | None:
    result = await db.execute(
        select(OperationCost).where(OperationCost.id == cost_id)
    )
    return result.scalar_one_or_none()


async def create_cost(
    db: AsyncSession,
    vendor: str,
    operation: str,
    base_cost: float,
    description: str | None = None,
) -> OperationCost:
    row = OperationCost(
        vendor=vendor,
        operation=operation,
        base_cost=base_cost,
        description=description,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await load_cost_cache(db)
    return row


async def update_cost(
    db: AsyncSession,
    row: OperationCost,
    base_cost: float | None = None,
    description: str | None = None,
) -> OperationCost:
    if base_cost is not None:
        row.base_cost = base_cost
    if description is not None:
        row.description = description
    row.updated_at = datetime.now()  # noqa: DTZ005 — DB stores with timezone via server_default
    await db.commit()
    await db.refresh(row)
    await load_cost_cache(db)
    return row


async def delete_cost(db: AsyncSession, row: OperationCost) -> None:
    await db.delete(row)
    await db.commit()
    await load_cost_cache(db)
