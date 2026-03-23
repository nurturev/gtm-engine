"""Execution router: single and batch enrichment operations."""

from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import get_current_tenant, require_credits
from server.auth.models import Tenant
from server.billing.service import check_and_hold, confirm_debit, release_hold
from server.core.database import get_db
from server.core.exceptions import ProviderError
from server.execution.schemas import (
    BatchExecuteRequest,
    BatchExecuteResponse,
    BatchStatusResponse,
    CostEstimateRequest,
    CostEstimateResponse,
    ExecuteRequest,
    ExecuteResponse,
)
from server.execution.service import (
    OPERATION_COSTS,
    SEARCH_OPERATIONS,
    BULK_OPERATIONS,
    calculate_cost,
    execute_single,
)
from server.execution.parallel import execute_batch
from server.execution.persistence import persist_execution
from server.execution.search_patterns import get_search_patterns

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["execute"])

# ---------------------------------------------------------------------------
# V1 batch size cap — prevents long-running requests during rolling restarts.
# 25 records ≈ 10-15s execution (safe within 30s grace window).
# V2: async job queue for larger batches.
# ---------------------------------------------------------------------------

MAX_BATCH_SIZE = 25

# ---------------------------------------------------------------------------
# In-memory batch store (replace with Redis/DB in production)
# ---------------------------------------------------------------------------

_batches: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/execute",
    response_model=ExecuteResponse,
    dependencies=[Depends(require_credits(1.0))],
)
async def execute_operation(
    request: Request,
    body: ExecuteRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
) -> ExecuteResponse:
    """Execute a single enrichment or search operation.

    Flow:
    1. Place a credit hold for the estimated cost
    2. Call the provider via the execution service
    3. On success: confirm the debit
    4. On failure: release the hold
    """
    execution_id = f"exec_{secrets.token_hex(8)}"
    start_time = time.monotonic()

    # Dynamic cost based on operation + params (per_page, batch size, etc.)
    estimated_cost = calculate_cost(body.operation, body.params)

    # Step 1: Hold credits (hold the estimated cost upfront)
    workflow_id = request.headers.get("X-Workflow-Id") or getattr(request.state, "workflow_id", None)
    user_id = getattr(request.state, "user_id", None)
    hold_id: int | None = None
    try:
        hold_id = await check_and_hold(db, tenant.id, estimated_cost, body.operation, workflow_id=workflow_id, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(exc),
        ) from exc

    # Step 2: Execute the operation
    try:
        result = await execute_single(
            db=db,
            operation=body.operation,
            provider_name=body.provider,
            params=body.params,
            tenant_id=tenant.id,
        )
    except ProviderError as exc:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        # Release the hold on provider failure
        if hold_id is not None:
            try:
                await release_hold(db, hold_id)
            except Exception:
                logger.exception("Failed to release hold %s", hold_id)
        # Log the failure
        await persist_execution(
            db,
            tenant_id=tenant.id,
            execution_id=execution_id,
            operation=body.operation,
            provider=body.provider or "unknown",
            is_byok=False,
            params=body.params,
            result_data=None,
            status="failed",
            error_message=str(exc),
            duration_ms=duration_ms,
        )
        raise HTTPException(
            status_code=exc.status_code or status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        # Release hold on any unexpected failure
        if hold_id is not None:
            try:
                await release_hold(db, hold_id)
            except Exception:
                logger.exception("Failed to release hold %s", hold_id)
        # Log the failure
        await persist_execution(
            db,
            tenant_id=tenant.id,
            execution_id=execution_id,
            operation=body.operation,
            provider=body.provider or "unknown",
            is_byok=False,
            params=body.params,
            result_data=None,
            status="failed",
            error_message=str(exc),
            duration_ms=duration_ms,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Execution failed: {exc}",
        ) from exc

    duration_ms = int((time.monotonic() - start_time) * 1000)

    # Step 3: Determine billing
    # - Cache hits: always free (no API call was made)
    # - BYOK calls: always free (user's own key)
    # - Platform key calls: charge the actual cost (based on what was fetched)
    is_byok = result.get("is_byok", False)
    is_cached = result.get("cached", False)
    actual_cost = result.get("actual_cost", estimated_cost)
    credits_charged = 0.0

    if is_cached or is_byok:
        # No charge — release the hold
        try:
            await release_hold(db, hold_id)
        except Exception:
            logger.exception("Failed to release hold %s", hold_id)
    else:
        # Platform key call — confirm the debit
        try:
            await confirm_debit(db, hold_id)
            credits_charged = actual_cost
        except Exception:
            logger.exception("Failed to confirm debit for hold %s", hold_id)

    # Step 4: Persist — log execution + upsert contacts/companies + cache searches
    await persist_execution(
        db,
        tenant_id=tenant.id,
        execution_id=execution_id,
        operation=body.operation,
        provider=result.get("provider", body.provider or "unknown"),
        is_byok=is_byok,
        params=body.params,
        result_data=result.get("data"),
        status="cached" if is_cached else "success",
        credits_charged=credits_charged,
        duration_ms=duration_ms,
        cached=is_cached,
    )

    return ExecuteResponse(
        execution_id=execution_id,
        status="success",
        credits_charged=credits_charged,
        result=result.get("data", result),
    )


@router.post("/execute/cost", response_model=CostEstimateResponse)
async def estimate_cost(
    body: CostEstimateRequest,
    tenant: Tenant = Depends(get_current_tenant),
) -> CostEstimateResponse:
    """Estimate the credit cost for an operation before executing it.

    Useful for CLI dry-run and batch cost preview. Returns the estimated
    cost and a human-readable breakdown of how it was calculated.
    """
    cost = calculate_cost(body.operation, body.params)

    # Build breakdown explanation
    if body.operation in SEARCH_OPERATIONS:
        per_page = int(body.params.get("per_page") or body.params.get("limit") or 25)
        page = int(body.params.get("page") or 1)
        breakdown = (
            f"Search: {per_page} results/page × 1 credit per 25 results = "
            f"{cost:.1f} credits (page {page})"
        )
    elif body.operation in BULK_OPERATIONS:
        if body.operation == "bulk_enrich_people":
            count = len(body.params.get("details", []))
        else:
            count = len(body.params.get("domains", []))
        breakdown = f"Bulk: {count} records × 1 credit each = {cost:.1f} credits"
    else:
        breakdown = f"Single enrichment = {cost:.1f} credit"

    return CostEstimateResponse(
        operation=body.operation,
        estimated_credits=cost,
        breakdown=breakdown,
        is_free_with_byok=True,
    )


@router.post(
    "/execute/batch",
    response_model=BatchExecuteResponse,
    dependencies=[Depends(require_credits(1.0))],
)
async def execute_batch_endpoint(
    request: Request,
    body: BatchExecuteRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
) -> BatchExecuteResponse:
    """Execute multiple records of the same operation concurrently.

    All operations in the batch must be the same type (e.g., all enrich_person).
    Records are processed concurrently with a concurrency limit of 5 to
    respect upstream rate limits. Each record goes through the full pipeline:
    cache check → rate limit → retry → normalize → cache store.

    Accepts a list of ExecuteRequest objects. All must share the same
    operation and provider — the concurrency engine sends them to the
    same API endpoint in parallel.
    """
    if not body.operations:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No operations provided",
        )

    if len(body.operations) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Batch size {len(body.operations)} exceeds maximum of "
                f"{MAX_BATCH_SIZE} records. Split into smaller batches or "
                f"use nRev for large-scale operations."
            ),
        )

    # All operations must be the same type for concurrent execution
    operation = body.operations[0].operation
    provider = body.operations[0].provider

    # Estimate total cost
    total_estimated_cost = sum(
        calculate_cost(op.operation, op.params) for op in body.operations
    )

    # Hold credits for the full batch
    workflow_id = request.headers.get("X-Workflow-Id") or getattr(request.state, "workflow_id", None)
    user_id = getattr(request.state, "user_id", None)
    hold_id: int | None = None
    try:
        hold_id = await check_and_hold(db, tenant.id, total_estimated_cost, "batch", workflow_id=workflow_id, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(exc),
        ) from exc

    try:
        # Extract records (param dicts) from the batch
        records = [op.params for op in body.operations]

        # Run the concurrent batch executor
        checkpoint = await execute_batch(
            db=db,
            operation=operation,
            provider_name=provider,
            records=records,
            tenant_id=tenant.id,
            concurrency=5,
            checkpoint_every=10,
            timeout_seconds=300.0,
        )
    except Exception as exc:
        logger.exception("Batch execution failed")
        if hold_id is not None:
            try:
                await release_hold(db, hold_id)
            except Exception:
                logger.exception("Failed to release batch hold")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Batch execution failed: {exc}",
        ) from exc

    # Billing: check if all results were BYOK/cached (free) or need charging
    all_free = all(
        r.is_byok or r.cached
        for r in checkpoint.results
        if r.status == "success"
    )

    if hold_id is not None:
        try:
            if all_free or checkpoint.cost_so_far == 0:
                await release_hold(db, hold_id)
            else:
                await confirm_debit(db, hold_id)
        except Exception:
            logger.exception("Failed to settle batch billing")

    # Build results for the response
    results = []
    for rr in checkpoint.results:
        entry: dict[str, Any] = {
            "execution_id": f"exec_{secrets.token_hex(4)}",
            "status": rr.status,
            "operation": rr.operation,
            "provider": rr.provider,
            "cached": rr.cached,
            "cost": rr.cost,
        }
        if rr.status == "success":
            entry["data"] = rr.data
        else:
            entry["error"] = rr.error
        results.append(entry)

    batch_id = checkpoint.batch_id
    _batches[batch_id] = {
        "total": checkpoint.total,
        "completed": checkpoint.completed,
        "failed": checkpoint.failed,
        "cached": checkpoint.cached,
        "status": "completed",
        "results": results,
        "total_cost": checkpoint.cost_so_far,
        "elapsed_ms": round(checkpoint.elapsed_ms, 1),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    return BatchExecuteResponse(
        batch_id=batch_id,
        total=checkpoint.total,
        status="completed",
    )


@router.get("/execute/batch/{batch_id}", response_model=BatchStatusResponse)
async def get_batch_status(
    batch_id: str,
    tenant: Tenant = Depends(get_current_tenant),
) -> BatchStatusResponse:
    """Poll the status of a batch execution."""
    batch = _batches.get(batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Batch {batch_id} not found",
        )
    return BatchStatusResponse(
        batch_id=batch_id,
        total=batch["total"],
        completed=batch["completed"],
        failed=batch["failed"],
        status=batch["status"],
        results=batch["results"],
    )


# ---------------------------------------------------------------------------
# Search patterns — platform-specific query intelligence
# ---------------------------------------------------------------------------


@router.get("/search/patterns")
async def search_patterns(
    platform: str | None = Query(None, description="Filter by platform: linkedin_jobs, twitter_posts, etc."),
    use_case: str | None = Query(None, description="Filter by GTM use case: hiring_signals, funding_news, etc."),
    tenant: Tenant = Depends(get_current_tenant),
) -> JSONResponse:
    """Return platform-specific Google search patterns and GTM query intelligence.

    This endpoint provides Claude with the knowledge of HOW to construct
    optimal Google search queries for different platforms and GTM use cases.
    The patterns evolve server-side without requiring client updates.

    Examples:
        GET /api/v1/search/patterns                      → full reference
        GET /api/v1/search/patterns?platform=linkedin_jobs → LinkedIn job patterns
        GET /api/v1/search/patterns?use_case=hiring_signals → hiring signal patterns
    """
    return JSONResponse(get_search_patterns(platform=platform, use_case=use_case))
