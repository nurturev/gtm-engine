"""Concurrent batch execution engine.

When you have 20 people to enrich or 50 companies to look up, this module
runs them in parallel against the SAME provider with:

- **Concurrency control**: Semaphore limits concurrent calls (default 5)
  to respect provider rate limits without overwhelming them.
- **Per-record error isolation**: One failed record doesn't kill the batch.
  Failures are captured and returned alongside successes.
- **Checkpointing**: Progress saved every N records. Large batches can
  report progress incrementally (e.g., "50/200 done, cost so far: $1.50").
- **Cost aggregation**: Tracks total cost, cache hits, and per-record costs.
- **Timeout protection**: Global timeout prevents runaway batches.

The execution pipeline for EACH record is still the full service.py pipeline:
    rate_limit → cache_check → resolve_key → retry(provider.execute) → normalize → cache_store

So every record benefits from caching (duplicates are free), rate limiting
(won't overwhelm upstream), and retry (transient failures auto-heal).

Usage:
    checkpoint = await execute_batch(
        db=db,
        operation="enrich_person",
        provider_name="apollo",
        records=[
            {"email": "jane@acme.com"},
            {"email": "john@acme.com"},
            ...  # 20 records
        ],
        tenant_id="tenant-xyz",
        concurrency=5,          # 5 concurrent calls
        checkpoint_every=10,    # progress update every 10 records
    )
    print(f"{checkpoint.completed}/{checkpoint.total} done, cost: {checkpoint.cost_so_far}")
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from sqlalchemy.ext.asyncio import AsyncSession

from server.execution.service import execute_single

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RecordResult:
    """Result from a single record execution within a batch."""

    index: int
    status: str  # "success" | "error"
    provider: str = ""
    operation: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    cached: bool = False
    is_byok: bool = False
    cost: float = 0.0
    latency_ms: float = 0.0


@dataclass
class BatchCheckpoint:
    """Tracks progress for a batch operation.

    This is the primary return type. It gives you:
    - Overall stats (completed, failed, cost)
    - Per-record results (for the caller to process)
    - Error details (for retry or reporting)
    """

    batch_id: str
    operation: str
    provider: str
    total: int
    completed: int = 0
    failed: int = 0
    cached: int = 0
    results: list[RecordResult] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    cost_so_far: float = 0.0
    elapsed_ms: float = 0.0


# ---------------------------------------------------------------------------
# Core batch executor
# ---------------------------------------------------------------------------


async def execute_batch(
    db: AsyncSession,
    operation: str,
    provider_name: str | None,
    records: list[dict[str, Any]],
    tenant_id: str,
    *,
    concurrency: int = 5,
    checkpoint_every: int = 10,
    timeout_seconds: float = 300.0,
    on_checkpoint: Callable[[BatchCheckpoint], Awaitable[None]] | None = None,
    skip_byok: bool = False,
) -> BatchCheckpoint:
    """Execute the same operation for many records concurrently.

    This is the main entry point for batch operations. It runs up to
    `concurrency` records at a time against the same provider, with
    checkpointing every `checkpoint_every` records.

    The execution pipeline for each record is the full service.py pipeline,
    so every record gets: cache check → rate limit → retry → normalize → cache store.

    Args:
        db: Database session for key resolution and billing.
        operation: The operation to run (e.g. "enrich_person").
        provider_name: Provider to use. None = use default for the operation.
        records: List of param dicts, one per record.
                 e.g. [{"email": "a@b.com"}, {"email": "c@d.com"}]
        tenant_id: The tenant making the request.
        concurrency: Max simultaneous API calls. Recommended:
                     - Apollo: 5 (rate limit ~10 req/s burst)
                     - RocketReach: 3 (global 10 req/s across all endpoints)
                     - PredictLeads: 5 (plan-dependent)
        checkpoint_every: Fire on_checkpoint callback after this many records.
        timeout_seconds: Global timeout for the entire batch (default 5 min).
        on_checkpoint: Optional async callback for progress reporting.

    Returns:
        BatchCheckpoint with all results and aggregate stats.

    Example:
        checkpoint = await execute_batch(
            db, "enrich_person", "apollo",
            [{"email": "jane@acme.com"}, {"email": "bob@corp.com"}],
            tenant_id="t-123",
            concurrency=5,
        )
        for r in checkpoint.results:
            if r.status == "success":
                print(r.data["name"], r.data.get("email"))
    """
    batch_id = f"batch_{secrets.token_hex(8)}"
    start = time.monotonic()

    checkpoint = BatchCheckpoint(
        batch_id=batch_id,
        operation=operation,
        provider=provider_name or "default",
        total=len(records),
    )

    if not records:
        return checkpoint

    semaphore = asyncio.Semaphore(concurrency)

    async def _process_record(params: dict[str, Any], index: int) -> RecordResult:
        """Process a single record through the full execution pipeline."""
        async with semaphore:
            t0 = time.monotonic()
            try:
                result = await execute_single(
                    db=db,
                    operation=operation,
                    provider_name=provider_name,
                    params=params,
                    tenant_id=tenant_id,
                    skip_byok=skip_byok,
                )
                latency = (time.monotonic() - t0) * 1000
                return RecordResult(
                    index=index,
                    status="success",
                    provider=result.get("provider", provider_name or "unknown"),
                    operation=operation,
                    data=result.get("data", {}),
                    cached=result.get("cached", False),
                    is_byok=result.get("is_byok", False),
                    cost=result.get("actual_cost", 0.0),
                    latency_ms=latency,
                )
            except Exception as exc:
                latency = (time.monotonic() - t0) * 1000
                logger.warning(
                    "Batch %s record %d/%d failed: %s",
                    batch_id, index + 1, len(records), exc,
                )
                return RecordResult(
                    index=index,
                    status="error",
                    provider=provider_name or "unknown",
                    operation=operation,
                    error=str(exc),
                    latency_ms=latency,
                )

    # Process in chunks for checkpointing
    # Within each chunk, records run concurrently (up to semaphore limit)
    for chunk_start in range(0, len(records), checkpoint_every):
        chunk_end = min(chunk_start + checkpoint_every, len(records))
        chunk = records[chunk_start:chunk_end]

        # Launch all records in this chunk concurrently
        tasks = [
            _process_record(params, chunk_start + i)
            for i, params in enumerate(chunk)
        ]

        try:
            remaining_timeout = timeout_seconds - (time.monotonic() - start)
            if remaining_timeout <= 0:
                logger.warning("Batch %s timed out at record %d", batch_id, chunk_start)
                # Mark remaining as errors
                for i in range(chunk_start, len(records)):
                    checkpoint.results.append(RecordResult(
                        index=i,
                        status="error",
                        operation=operation,
                        error="Batch timeout exceeded",
                    ))
                    checkpoint.failed += 1
                break

            chunk_results = await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=remaining_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Batch %s timed out during chunk %d-%d", batch_id, chunk_start, chunk_end)
            for i in range(chunk_start, len(records)):
                checkpoint.results.append(RecordResult(
                    index=i,
                    status="error",
                    operation=operation,
                    error="Batch timeout exceeded",
                ))
                checkpoint.failed += 1
            break

        # Collect results
        for rr in chunk_results:
            checkpoint.results.append(rr)
            if rr.status == "success":
                checkpoint.completed += 1
                checkpoint.cost_so_far += rr.cost
                if rr.cached:
                    checkpoint.cached += 1
            else:
                checkpoint.failed += 1
                checkpoint.errors.append({
                    "index": rr.index,
                    "error": rr.error,
                })

        # Fire checkpoint callback for progress reporting
        if on_checkpoint is not None:
            try:
                await on_checkpoint(checkpoint)
            except Exception:
                logger.warning("Checkpoint callback failed", exc_info=True)

        logger.info(
            "Batch %s progress: %d/%d done (%d cached, %d failed), cost: %.2f credits",
            batch_id,
            checkpoint.completed + checkpoint.failed,
            checkpoint.total,
            checkpoint.cached,
            checkpoint.failed,
            checkpoint.cost_so_far,
        )

    checkpoint.elapsed_ms = (time.monotonic() - start) * 1000
    return checkpoint
