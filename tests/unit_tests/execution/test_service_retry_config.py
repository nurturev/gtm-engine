"""Unit tests for the ``provider.retry_config`` threading in ``execute_single``.

HLD §6.2 introduces a per-provider ``retry_config`` on ``BaseProvider`` that
``execute_single`` must thread into ``retry_with_backoff``. Providers without
an override fall back to the global defaults. This is shared infra (T03
Option A): Fresh LinkedIn uses it to realise D22 (one retry after ~60s), and
any future provider that wants a non-default retry policy gets it for free.

Blueprint §7 "Application / orchestration glue ... conditional dispatch ...
test it." Spy pattern for ``retry_with_backoff`` to capture threaded kwargs.
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
from server.execution.retry import RetryConfig


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _DefaultRetryFakeProvider(BaseProvider):
    """A provider with NO retry_config override — must use global defaults."""

    name = "unit_fake_default_retry"
    supported_operations = ["enrich_person"]
    cacheable = False  # bypass the cache so the test is deterministic

    async def execute(
        self, operation: str, params: dict[str, Any], api_key: str
    ) -> dict[str, Any]:
        return {"person": {"name": "Jane"}}

    async def health_check(self, api_key: str) -> bool:
        return True


class _CustomRetryFakeProvider(BaseProvider):
    """A provider with a custom retry_config — must be threaded through."""

    name = "unit_fake_custom_retry"
    supported_operations = ["enrich_person"]
    cacheable = False
    retry_config = RetryConfig(
        max_retries=1,
        base_delay=60.0,
        max_delay=60.0,
        jitter=False,
    )

    async def execute(
        self, operation: str, params: dict[str, Any], api_key: str
    ) -> dict[str, Any]:
        return {"person": {"name": "Jane"}}

    async def health_check(self, api_key: str) -> bool:
        return True


class _RetrySpy:
    """Captures the retry-config kwargs handed to ``retry_with_backoff``.

    Signature mirrors ``retry_with_backoff`` itself so its own config kwargs
    are consumed here and never leak into ``func``. Real-callable surface —
    invokes the target function so the pipeline continues normally.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        func,
        *args,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        jitter: bool = True,
        retryable_exceptions=(Exception,),
        **kwargs,
    ):
        self.calls.append(
            {
                "max_retries": max_retries,
                "base_delay": base_delay,
                "max_delay": max_delay,
                "jitter": jitter,
                "retryable_exceptions": retryable_exceptions,
            }
        )
        return await func(*args, **kwargs)


# ---------------------------------------------------------------------------
# Fixtures — neutralise unrelated collaborators (same pattern as cache tests)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _neutralise_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.execution import service

    monkeypatch.setattr(service, "_get_rate_limiter", lambda: None)


@pytest.fixture(autouse=True)
def _neutralise_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.execution import service

    monkeypatch.setattr(service, "_get_cache", lambda: None)


@pytest.fixture(autouse=True)
def _neutralise_api_key_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.execution import service

    async def _fake_resolve_api_key(db, tenant_id, provider_name, skip_byok=False):
        return ("test-key", True)

    monkeypatch.setattr(service, "resolve_api_key", _fake_resolve_api_key)


@pytest.fixture
def retry_spy(monkeypatch: pytest.MonkeyPatch) -> _RetrySpy:
    spy = _RetrySpy()
    from server.execution import service

    monkeypatch.setattr(service, "retry_with_backoff", spy)
    return spy


@pytest.fixture
def register_default_provider():
    register_provider(_DefaultRetryFakeProvider.name, _DefaultRetryFakeProvider)
    yield _DefaultRetryFakeProvider


@pytest.fixture
def register_custom_provider():
    register_provider(_CustomRetryFakeProvider.name, _CustomRetryFakeProvider)
    yield _CustomRetryFakeProvider


# ---------------------------------------------------------------------------
# Tests — default path
# ---------------------------------------------------------------------------


class TestProviderWithoutRetryConfigUsesGlobalDefaults:
    async def test_threads_global_defaults(
        self, retry_spy: _RetrySpy, register_default_provider
    ) -> None:
        from server.execution.service import execute_single

        await execute_single(
            db=None,
            operation="enrich_person",
            provider_name="unit_fake_default_retry",
            params={"linkedin_url": "https://www.linkedin.com/in/janedoe"},
            tenant_id="tenant-1",
        )

        assert len(retry_spy.calls) == 1
        kwargs = retry_spy.calls[0]
        # Global defaults from server/execution/retry.py docstring.
        assert kwargs.get("max_retries") == 3
        assert kwargs.get("base_delay") == 1.0
        assert kwargs.get("max_delay") == 30.0
        assert kwargs.get("jitter") is True


# ---------------------------------------------------------------------------
# Tests — overridden path (D22 for fresh_linkedin)
# ---------------------------------------------------------------------------


class TestProviderWithRetryConfigOverridesDefaults:
    async def test_threads_overridden_max_retries(
        self, retry_spy: _RetrySpy, register_custom_provider
    ) -> None:
        from server.execution.service import execute_single

        await execute_single(
            db=None,
            operation="enrich_person",
            provider_name="unit_fake_custom_retry",
            params={"linkedin_url": "https://www.linkedin.com/in/janedoe"},
            tenant_id="tenant-1",
        )

        assert retry_spy.calls[0].get("max_retries") == 1

    async def test_threads_overridden_base_delay(
        self, retry_spy: _RetrySpy, register_custom_provider
    ) -> None:
        from server.execution.service import execute_single

        await execute_single(
            db=None,
            operation="enrich_person",
            provider_name="unit_fake_custom_retry",
            params={"linkedin_url": "https://www.linkedin.com/in/janedoe"},
            tenant_id="tenant-1",
        )

        assert retry_spy.calls[0].get("base_delay") == 60.0

    async def test_disables_jitter_when_overridden(
        self, retry_spy: _RetrySpy, register_custom_provider
    ) -> None:
        """D22: a deterministic 60s wait, not a jittered-around-60s wait. A
        jittered delay could be ~30s, which is visible to the user as a short
        stall followed by an immediate 429 — the opposite of the intent."""
        from server.execution.service import execute_single

        await execute_single(
            db=None,
            operation="enrich_person",
            provider_name="unit_fake_custom_retry",
            params={"linkedin_url": "https://www.linkedin.com/in/janedoe"},
            tenant_id="tenant-1",
        )

        assert retry_spy.calls[0].get("jitter") is False
