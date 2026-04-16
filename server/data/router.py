"""Tables router: list, query, and raw SQL access to tenant data."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.flexible import get_tenant_flexible as _get_tenant_flexible
from server.auth.models import Tenant
from server.core.database import get_db, set_tenant_context
from server.data.schemas import (
    RawQueryRequest,
    RawQueryResponse,
    TableListResponse,
    TableQueryResponse,
)

router = APIRouter(prefix="/api/v1", tags=["tables"])

ALLOWED_TABLES = {"contacts", "companies", "search_results", "enrichment_log"}

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/tables", response_model=TableListResponse)
async def list_tables(
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> TableListResponse:
    """Return the list of interactive tables available to the tenant."""
    await _get_tenant_flexible(request, token, db)
    return TableListResponse(tables=sorted(ALLOWED_TABLES))


@router.get("/tables/{table}", response_model=TableQueryResponse)
async def query_table(
    table: str,
    request: Request,
    limit: int = 100,
    offset: int = 0,
    order_by: str | None = None,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> TableQueryResponse:
    """Query a specific interactive table with optional ordering and pagination.

    RLS ensures only the current tenant's rows are visible.
    """
    tenant, db = await _get_tenant_flexible(request, token, db)

    if table not in ALLOWED_TABLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Table '{table}' is not queryable. Allowed: {sorted(ALLOWED_TABLES)}",
        )

    from sqlalchemy import text

    # Build safe query
    order_clause = ""
    if order_by:
        # Only allow simple column names (no SQL injection)
        col = order_by.lstrip("-")
        if not col.isidentifier():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid order_by column name",
            )
        direction = "DESC" if order_by.startswith("-") else "ASC"
        order_clause = f" ORDER BY {col} {direction}"

    count_result = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))  # noqa: S608
    total = count_result.scalar_one()

    query = text(
        f"SELECT * FROM {table}{order_clause} LIMIT :limit OFFSET :offset"  # noqa: S608
    )
    result = await db.execute(query, {"limit": limit, "offset": offset})
    rows = [dict(row._mapping) for row in result.fetchall()]

    return TableQueryResponse(table=table, rows=rows, total=total)


@router.post("/query", response_model=RawQueryResponse)
async def execute_raw_query(
    body: RawQueryRequest,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> RawQueryResponse:
    """Execute a read-only SQL query scoped to the current tenant via RLS.

    Only SELECT statements are permitted.
    """
    tenant, db = await _get_tenant_flexible(request, token, db)

    normalized = body.sql.strip().upper()
    if not normalized.startswith("SELECT"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only SELECT queries are allowed",
        )

    # Block dangerous keywords
    for keyword in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE"):
        if keyword in normalized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Query contains forbidden keyword: {keyword}",
            )

    from sqlalchemy import text

    try:
        result = await db.execute(text(body.sql), body.params)
        rows = [dict(row._mapping) for row in result.fetchall()]
        columns = list(result.keys()) if rows else []
        return RawQueryResponse(columns=columns, rows=rows, row_count=len(rows))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Query error: {exc}",
        ) from exc
