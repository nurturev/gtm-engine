"""Unit tests for the ``provider.cacheable`` gate in ``execute_single``.

Blueprint §7 "Application / orchestration glue that contains logic —
conditional dispatch ... test it." The cache-gate is generic shared infra
(HLD §6.1): any provider with ``cacheable = False`` bypasses both read and
write; any provider with the default ``True`` continues to use the cache.

Fakes over mocks (blueprint §6 hierarchy tier 3). ``monkeypatch`` is used
only for the cross-cutting singletons that ``execute_single`` reaches through
module-level getters — the last-resort tier, justified because the production
code has no DI seam for these collaborators today.
"""

from __future__ import annotations

from typing import Any

import pytest

# ``server.execution.service`` pulls the SQLAlchemy async engine at import
# time, which needs ``asyncpg``. Skip the whole module when the backend dep
# isn't installed — these gate tests only make sense against a full server env.
pytest.importorskip("asyncpg")

from server.execution.providers import register_provider
from server.execution.providers.base import BaseProvider


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fakes — real classes with real signatures, not Mock objects
# ---------------------------------------------------------------------------


class _CacheableFakeProvider(BaseProvider):
    """A minimal provider whose ``cacheable`` flag we can flip per test."""

    name = "unit_fake_cacheable"
    supported_operations = ["enrich_person"]
    # `cacheable` is set per-test before registration (see fixtures below).

    execute_call_count = 0

    async def execute(
        self, operation: str, params: dict[str, Any], api_key: str
    ) -> dict[str, Any]:
        type(self).execute_call_count += 1
        return {"person": {"name": "Upstream Jane"}}

    async def health_check(self, api_key: str) -> bool:
        return True


class _UncacheableFakeProvider(BaseProvider):
    name = "unit_fake_uncacheable"
    supported_operations = ["enrich_person"]
    cacheable = False

    execute_call_count = 0

    async def execute(
        self, operation: str, params: dict[str, Any], api_key: str
    ) -> dict[str, Any]:
        type(self).execute_call_count += 1
        return {"person": {"name": "Upstream Jane"}}

    async def health_check(self, api_key: str) -> bool:
        return True


class _SpyCache:
    """Records every cache read/write. Spy pattern from the API-test blueprint
    — real signatures, no pretend-mock behaviour."""

    def __init__(self, prime: dict | None = None) -> None:
        self.reads: list[tuple] = []
        self.writes: list[tuple] = []
        self._store = prime or {}

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
# Fixtures — patch the cross-cutting singletons for the duration of one test
# ---------------------------------------------------------------------------


@pytest.fixture
def spy_cache(monkeypatch: pytest.MonkeyPatch) -> _SpyCache:
    """Replace the cache singleton with a spy. Returned so assertions can
    inspect reads/writes."""
    cache = _SpyCache()
    from server.execution import service

    monkeypatch.setattr(service, "_get_cache", lambda: cache)
    return cache


@pytest.fixture(autouse=True)
def _neutralise_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rate limiter is irrelevant to these tests — neutralise it."""
    from server.execution import service

    monkeypatch.setattr(service, "_get_rate_limiter", lambda: None)


@pytest.fixture(autouse=True)
def _neutralise_api_key_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Key lookup touches the DB and vault — short-circuit both."""
    from server.execution import service

    async def _fake_resolve_api_key(db, tenant_id, provider_name, skip_byok=False):
        return ("test-key", True)  # byok=True → no billing debit path exercised

    monkeypatch.setattr(service, "resolve_api_key", _fake_resolve_api_key)


@pytest.fixture(autouse=True)
def _neutralise_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass retry backoff — we want the provider.execute to run immediately
    so the test doesn't wait on real sleeps, and we don't care about retry
    behaviour in this file (covered in test_service_retry_config.py).

    The replacement mirrors ``retry_with_backoff``'s signature so its own
    config kwargs (``max_retries`` etc.) are consumed here and never leak
    into ``func`` — which would fail since ``provider.execute`` doesn't
    accept them.
    """
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


@pytest.fixture
def register_cacheable_provider():
    """Register a fresh copy of the cacheable fake for the test and reset
    its call counter. Tests must run in parallel without cross-talk
    (blueprint §5.5)."""
    _CacheableFakeProvider.execute_call_count = 0
    # `cacheable` is the BaseProvider default (True). Assert it here so
    # a stray change to the base class doesn't silently weaken the test.
    assert _CacheableFakeProvider.cacheable is True
    register_provider(_CacheableFakeProvider.name, _CacheableFakeProvider)
    yield _CacheableFakeProvider


@pytest.fixture
def register_uncacheable_provider():
    _UncacheableFakeProvider.execute_call_count = 0
    assert _UncacheableFakeProvider.cacheable is False
    register_provider(_UncacheableFakeProvider.name, _UncacheableFakeProvider)
    yield _UncacheableFakeProvider


# ---------------------------------------------------------------------------
# Tests — cacheable == True (the default)
# ---------------------------------------------------------------------------


class TestCacheableProviderUsesCache:
    async def test_reads_cache_on_request(
        self, spy_cache: _SpyCache, register_cacheable_provider
    ) -> None:
        from server.execution.service import execute_single

        await execute_single(
            db=None,
            operation="enrich_person",
            provider_name="unit_fake_cacheable",
            params={"linkedin_url": "https://www.linkedin.com/in/janedoe"},
            tenant_id="tenant-1",
        )

        assert len(spy_cache.reads) == 1, "cacheable provider must consult the cache"

    async def test_writes_result_to_cache_on_miss(
        self, spy_cache: _SpyCache, register_cacheable_provider
    ) -> None:
        from server.execution.service import execute_single

        await execute_single(
            db=None,
            operation="enrich_person",
            provider_name="unit_fake_cacheable",
            params={"linkedin_url": "https://www.linkedin.com/in/janedoe"},
            tenant_id="tenant-1",
        )

        assert len(spy_cache.writes) == 1, "cacheable provider must populate the cache on miss"

    async def test_cache_hit_short_circuits_provider(
        self, monkeypatch: pytest.MonkeyPatch, register_cacheable_provider
    ) -> None:
        from server.execution import service

        params = {"linkedin_url": "https://www.linkedin.com/in/janedoe"}
        key = ("tenant-1", "enrich_person", tuple(sorted(params.items())))
        primed = _SpyCache(
            prime={key: {"data": {"name": "Cached Jane"}, "is_byok": True}}
        )
        monkeypatch.setattr(service, "_get_cache", lambda: primed)

        result = await service.execute_single(
            db=None,
            operation="enrich_person",
            provider_name="unit_fake_cacheable",
            params=params,
            tenant_id="tenant-1",
        )

        assert _CacheableFakeProvider.execute_call_count == 0, (
            "cache hit must short-circuit the provider"
        )
        assert result.get("cached") is True


# ---------------------------------------------------------------------------
# Tests — cacheable == False (fresh_linkedin's policy)
# ---------------------------------------------------------------------------


class TestUncacheableProviderBypassesCache:
    async def test_does_not_read_cache(
        self, spy_cache: _SpyCache, register_uncacheable_provider
    ) -> None:
        from server.execution.service import execute_single

        await execute_single(
            db=None,
            operation="enrich_person",
            provider_name="unit_fake_uncacheable",
            params={"linkedin_url": "https://www.linkedin.com/in/janedoe"},
            tenant_id="tenant-1",
        )

        assert spy_cache.reads == [], (
            "cacheable=False must skip cache reads — D17 for fresh_linkedin"
        )

    async def test_does_not_write_cache(
        self, spy_cache: _SpyCache, register_uncacheable_provider
    ) -> None:
        from server.execution.service import execute_single

        await execute_single(
            db=None,
            operation="enrich_person",
            provider_name="unit_fake_uncacheable",
            params={"linkedin_url": "https://www.linkedin.com/in/janedoe"},
            tenant_id="tenant-1",
        )

        assert spy_cache.writes == [], (
            "cacheable=False must skip cache writes — D17 for fresh_linkedin"
        )

    async def test_second_identical_call_hits_upstream_again(
        self, spy_cache: _SpyCache, register_uncacheable_provider
    ) -> None:
        """D17 consequence: the same LinkedIn URL twice in a minute must
        consume upstream quota twice — freshness is the whole point."""
        from server.execution.service import execute_single

        params = {"linkedin_url": "https://www.linkedin.com/in/janedoe"}

        await execute_single(
            db=None, operation="enrich_person",
            provider_name="unit_fake_uncacheable",
            params=params, tenant_id="tenant-1",
        )
        await execute_single(
            db=None, operation="enrich_person",
            provider_name="unit_fake_uncacheable",
            params=params, tenant_id="tenant-1",
        )

        assert _UncacheableFakeProvider.execute_call_count == 2
