"""API tests for POST /api/v1/execute/batch response shape.

The batch endpoint used to return only ``{batch_id, total, status}`` and
force callers to poll GET /execute/batch/{id} for the actual payload —
even though the handler ran the batch synchronously and had every result
in hand before returning. These tests lock in the fix: the POST response
now carries ``results``, ``completed``, and ``failed`` inline, and the
GET endpoint continues to serve the same shape for backward-compat
pollers (e.g. workflow_studio's consultant agent).

The handler is exercised directly (bypassing the FastAPI dependency
graph) so the test has no DB / auth / billing dependencies — we're
asserting on response construction, not on auth or billing behaviour,
both of which have their own test coverage.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("asyncpg")  # server.execution.* pulls asyncpg at import

from fastapi import Request

from server.auth.dependencies import TenantRef
from server.execution.parallel import BatchCheckpoint, RecordResult
from server.execution.router import _batches, execute_batch_endpoint, get_batch_status
from server.execution.schemas import (
    BatchExecuteRequest,
    BatchExecuteResponse,
    BatchStatusResponse,
    ExecuteRequest,
)


pytestmark = pytest.mark.asyncio


def _stub_request() -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [],
        "path": "/api/v1/execute/batch",
        "query_string": b"",
    }
    return Request(scope)


def _checkpoint(batch_id: str = "batch_test123") -> BatchCheckpoint:
    return BatchCheckpoint(
        batch_id=batch_id,
        operation="enrich_company",
        provider="apollo",
        total=2,
        completed=1,
        failed=1,
        cached=0,
        results=[
            RecordResult(
                index=0,
                status="success",
                provider="apollo",
                operation="enrich_company",
                data={"domain": "stripe.com", "name": "Stripe"},
                cached=False,
                cost=1.0,
            ),
            RecordResult(
                index=1,
                status="error",
                provider="apollo",
                operation="enrich_company",
                error="domain not found",
                cached=False,
                cost=0.0,
            ),
        ],
        cost_so_far=1.0,
        elapsed_ms=1234.5,
    )


def _body() -> BatchExecuteRequest:
    return BatchExecuteRequest(
        operations=[
            ExecuteRequest(
                operation="enrich_company",
                provider="apollo",
                params={"domain": "stripe.com"},
            ),
            ExecuteRequest(
                operation="enrich_company",
                provider="apollo",
                params={"domain": "not-a-real-domain-xyz.com"},
            ),
        ]
    )


async def _invoke(checkpoint: BatchCheckpoint) -> BatchExecuteResponse:
    """Call the POST handler with billing/db/execute_batch stubbed out."""
    request = _stub_request()
    tenant = TenantRef(id="tenant-1", is_service_token=False)
    db = MagicMock()

    with (
        patch(
            "server.execution.router.execute_batch",
            new=AsyncMock(return_value=checkpoint),
        ),
        patch(
            "server.execution.router.check_and_hold",
            new=AsyncMock(return_value=99),
        ),
        patch(
            "server.execution.router.confirm_debit",
            new=AsyncMock(),
        ),
        patch(
            "server.execution.router.release_hold",
            new=AsyncMock(),
        ),
        patch(
            "server.execution.router.set_tenant_context",
            new=AsyncMock(),
        ),
        # Force local-credit path so the platform-credit asyncio.create_task
        # branch doesn't fire during the test.
        patch("server.execution.router.settings.PLATFORM_CREDIT_SERVICE_URL", ""),
    ):
        return await execute_batch_endpoint(
            request=request, body=_body(), tenant=tenant, db=db
        )


async def test_post_returns_results_inline():
    """The core fix: results must come back on the POST, not via a second GET."""
    _batches.clear()

    response = await _invoke(_checkpoint("batch_inline_1"))

    assert isinstance(response, BatchExecuteResponse)
    assert response.batch_id == "batch_inline_1"
    assert response.total == 2
    assert response.status == "completed"
    assert response.completed == 1
    assert response.failed == 1

    assert len(response.results) == 2

    success = response.results[0]
    assert success["status"] == "success"
    assert success["operation"] == "enrich_company"
    assert success["provider"] == "apollo"
    assert success["cached"] is False
    assert success["cost"] == 1.0
    assert success["data"] == {"domain": "stripe.com", "name": "Stripe"}
    assert "execution_id" in success and success["execution_id"].startswith("exec_")

    failure = response.results[1]
    assert failure["status"] == "error"
    assert failure["error"] == "domain not found"
    assert failure["cost"] == 0.0
    # Failed entries carry an error, not a data payload.
    assert "data" not in failure


async def test_empty_batch_rejected():
    """Empty operations list must raise 400 before any billing hold is placed."""
    from fastapi import HTTPException

    request = _stub_request()
    tenant = TenantRef(id="tenant-1")
    db = MagicMock()
    body = BatchExecuteRequest(operations=[])

    with pytest.raises(HTTPException) as exc:
        await execute_batch_endpoint(
            request=request, body=body, tenant=tenant, db=db
        )
    assert exc.value.status_code == 400


async def test_get_endpoint_serves_same_payload_for_pollers():
    """workflow_studio's consultant agent polls the GET endpoint. That path
    must still return the stashed batch in the expected shape until it
    migrates to reading results inline from the POST."""
    _batches.clear()

    post_response = await _invoke(_checkpoint("batch_backcompat_1"))
    assert post_response.batch_id in _batches  # sanity

    get_response: BatchStatusResponse = await get_batch_status(
        batch_id=post_response.batch_id,
        tenant=TenantRef(id="tenant-1"),
    )

    assert get_response.batch_id == post_response.batch_id
    assert get_response.total == post_response.total
    assert get_response.completed == post_response.completed
    assert get_response.failed == post_response.failed
    assert get_response.status == post_response.status
    assert get_response.results == post_response.results


async def test_get_endpoint_404_for_unknown_batch():
    """Unknown batch_id → 404. Guards against silent empty responses if the
    in-memory store gets cleared between POST and GET (e.g. cross-pod)."""
    from fastapi import HTTPException

    _batches.clear()

    with pytest.raises(HTTPException) as exc:
        await get_batch_status(
            batch_id="batch_does_not_exist",
            tenant=TenantRef(id="tenant-1"),
        )
    assert exc.value.status_code == 404
