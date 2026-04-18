"""Unit tests for the in-progress billing + cache-skip orchestration in
``execute_single`` (LLD §3.10 T13 + §6.2 T15).

When RocketReach's async lookup loop caps out at 30s, it returns a partial
profile stamped with ``lookup_status: "in_progress"`` and a ``retry_hint``.
The orchestration layer must react to that signal in two distinct ways:

    1. **Zero credit cost** — the caller must retry, and vendor-side
       re-lookups are free; billing here would double-charge the tenant.
    2. **Skip cache write** — caching a partial ``in_progress`` payload
       would serve stale "still searching" data to every subsequent call.

Both decisions branch on ``normalized.get("lookup_status") == "in_progress"``.
These tests drive the real ``execute_single`` with a fake
``RocketReachProvider`` swapped into the registry, so the normalizer runs
for real and the orchestration is exercised end-to-end.

Fakes over mocks per backend-unit-testing-blueprint §6 tier 3.
"""

from __future__ import annotations

from typing import Any

import pytest

# ``server.execution.service`` imports SQLAlchemy async — skip the module
# if the asyncpg dep isn't installed (matches the existing cache-gate test).
pytest.importorskip("asyncpg")

from server.execution.providers import _registry, register_provider
from server.execution.providers.base import BaseProvider


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _InProgressRocketReachFake(BaseProvider):
    """A RocketReach stand-in that always returns the cap-hit partial
    payload. Used to exercise the in-progress branch of execute_single
    without having to drive the real HTTP + polling loop."""

    name = "rocketreach"
    supported_operations = [
        "enrich_person", "search_people", "enrich_company", "search_companies",
    ]

    async def execute(
        self, operation: str, params: dict[str, Any], api_key: str,
    ) -> dict[str, Any]:
        return {
            "id": 42,
            "name": "Priya (partial)",
            "lookup_status": "in_progress",
            "retry_hint": {"vendor_id": 42, "retry_after_seconds": 30},
        }

    async def health_check(self, api_key: str) -> bool:
        return True


class _CompleteRocketReachFake(BaseProvider):
    """The happy-path comparison — the same shape as the cap-hit fake
    without the ``lookup_status`` marker. Lets us confirm the gate fires
    only when the marker is present."""

    name = "rocketreach"
    supported_operations = [
        "enrich_person", "search_people", "enrich_company", "search_companies",
    ]

    async def execute(
        self, operation: str, params: dict[str, Any], api_key: str,
    ) -> dict[str, Any]:
        return {
            "id": 42, "name": "Priya Complete",
            "current_title": "CTO", "current_employer": "Acme",
            "emails": [{"email": "priya@acme.com", "grade": "A"}],
        }

    async def health_check(self, api_key: str) -> bool:
        return True


class _SpyCache:
    """Records every cache read/write. Mirrors the spy in
    test_service_cache_gate.py so behaviour is consistent across files."""

    def __init__(self) -> None:
        self.reads: list[tuple] = []
        self.writes: list[tuple] = []
        self._store: dict = {}

    async def get(self, tenant_id: str, operation: str, params: dict) -> dict | None:
        self.reads.append((tenant_id, operation, tuple(sorted(params.items()))))
        return self._store.get((tenant_id, operation, tuple(sorted(params.items()))))

    async def set(
        self,
        tenant_id: str,
        operation: str,
        params: dict,
        payload: dict,
        ttl: int,
    ) -> None:
        self.writes.append((tenant_id, operation, tuple(sorted(params.items())), ttl))
        self._store[(tenant_id, operation, tuple(sorted(params.items())))] = payload


# ---------------------------------------------------------------------------
# Fixtures — isolate the real rocketreach provider for the test duration,
# neutralise cross-cutting singletons we don't care about here.
# ---------------------------------------------------------------------------


@pytest.fixture
def spy_cache(monkeypatch: pytest.MonkeyPatch) -> _SpyCache:
    cache = _SpyCache()
    from server.execution import service

    monkeypatch.setattr(service, "_get_cache", lambda: cache)
    return cache


@pytest.fixture(autouse=True)
def _neutralise_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.execution import service

    monkeypatch.setattr(service, "_get_rate_limiter", lambda: None)


@pytest.fixture(autouse=True)
def _neutralise_api_key_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.execution import service

    async def _fake_resolve_api_key(db, tenant_id, provider_name, skip_byok=False):
        # is_byok=False so calculate_cost runs (byok calls are free anyway —
        # we want the non-byok branch to prove the gate, not the byok bypass).
        return ("test-key", False)

    monkeypatch.setattr(service, "resolve_api_key", _fake_resolve_api_key)


@pytest.fixture(autouse=True)
def _neutralise_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.execution import service

    async def _direct_call(
        func,
        *args,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        jitter: bool = True,
        retryable_exceptions=(Exception,),
        **kwargs,
    ):
        return await func(*args, **kwargs)

    monkeypatch.setattr(service, "retry_with_backoff", _direct_call)


@pytest.fixture(autouse=True)
def _stable_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``calculate_cost`` so the test doesn't depend on DB-loaded base
    costs and we can assert on exact numeric values. 3.0 matches the
    Universal migration's per-op price (requirements §5.4)."""
    from server.execution import service

    monkeypatch.setattr(service, "calculate_cost", lambda op, params, vendor=None: 3.0)


@pytest.fixture
def in_progress_rocketreach():
    """Swap the real rocketreach provider for the in-progress fake, then
    restore on teardown so other tests in the same process see the real
    provider again."""
    original = _registry.get("rocketreach")
    register_provider("rocketreach", _InProgressRocketReachFake)
    try:
        yield _InProgressRocketReachFake
    finally:
        if original is not None:
            register_provider("rocketreach", original)
        else:
            _registry.pop("rocketreach", None)


@pytest.fixture
def complete_rocketreach():
    original = _registry.get("rocketreach")
    register_provider("rocketreach", _CompleteRocketReachFake)
    try:
        yield _CompleteRocketReachFake
    finally:
        if original is not None:
            register_provider("rocketreach", original)
        else:
            _registry.pop("rocketreach", None)


# ---------------------------------------------------------------------------
# in_progress → actual_cost is zero
# ---------------------------------------------------------------------------


class TestInProgressLookupBillsZero:
    """LLD §3.10 T13: the router debits from ``actual_cost``, so zeroing
    it here covers every billing path."""

    async def test_actual_cost_is_zero_on_in_progress(
        self, spy_cache, in_progress_rocketreach
    ) -> None:
        from server.execution.service import execute_single

        result = await execute_single(
            db=None,
            operation="enrich_person",
            provider_name="rocketreach",
            params={"linkedin_url": "https://linkedin.com/in/priya"},
            tenant_id="tenant-1",
        )

        assert result["actual_cost"] == 0.0

    async def test_actual_cost_is_3_on_complete_lookup(
        self, spy_cache, complete_rocketreach
    ) -> None:
        """Regression guard: the gate must fire **only** on in_progress.
        A complete lookup must still bill the normal per-op price."""
        from server.execution.service import execute_single

        result = await execute_single(
            db=None,
            operation="enrich_person",
            provider_name="rocketreach",
            params={"linkedin_url": "https://linkedin.com/in/priya"},
            tenant_id="tenant-1",
        )

        assert result["actual_cost"] == 3.0

    async def test_in_progress_payload_surfaces_top_level_markers(
        self, spy_cache, in_progress_rocketreach
    ) -> None:
        """The normalizer elevates lookup_status + retry_hint to the top
        level of ``data`` so the router can gate billing without drilling
        into additional_data. Pin that contract."""
        from server.execution.service import execute_single

        result = await execute_single(
            db=None,
            operation="enrich_person",
            provider_name="rocketreach",
            params={"linkedin_url": "https://linkedin.com/in/priya"},
            tenant_id="tenant-1",
        )

        data = result["data"]
        assert data.get("lookup_status") == "in_progress"
        assert data.get("retry_hint", {}).get("vendor_id") == 42


# ---------------------------------------------------------------------------
# in_progress → cache write is skipped
# ---------------------------------------------------------------------------


class TestInProgressLookupSkipsCacheWrite:
    """LLD §6.2 T15. Caching a partial in-progress payload would mean the
    retry call served the same stale partial every time — and the tenant
    would pay zero credits while getting zero useful data."""

    async def test_no_cache_write_when_lookup_status_is_in_progress(
        self, spy_cache, in_progress_rocketreach
    ) -> None:
        from server.execution.service import execute_single

        await execute_single(
            db=None,
            operation="enrich_person",
            provider_name="rocketreach",
            params={"linkedin_url": "https://linkedin.com/in/priya"},
            tenant_id="tenant-1",
        )

        assert spy_cache.writes == [], (
            "in_progress payload must not be cached — the retry has to "
            "re-hit the vendor to get complete data"
        )

    async def test_cache_write_still_happens_on_complete_lookup(
        self, spy_cache, complete_rocketreach
    ) -> None:
        """The migration must not disable caching on the happy path —
        complete lookups continue to populate the cache as before."""
        from server.execution.service import execute_single

        await execute_single(
            db=None,
            operation="enrich_person",
            provider_name="rocketreach",
            params={"linkedin_url": "https://linkedin.com/in/priya"},
            tenant_id="tenant-1",
        )

        assert len(spy_cache.writes) == 1, (
            "complete rocketreach lookup must populate the cache"
        )
