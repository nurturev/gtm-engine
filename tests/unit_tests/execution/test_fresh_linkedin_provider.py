"""Unit tests for ``FreshLinkedInProvider`` entry-gate validation.

Scope: parameter validation at the entry of ``execute``. No HTTP happens on
these paths — validation must fail-fast before any network call.

Wire-level / response-handling tests (200, 404, 429 retry, 5xx) are API tests
per ``fresh_linkedin_lld.md`` §11.2 and live in
``tests/api_tests/test_execution_router_wiring.py``.

Contract (from ``fresh_linkedin_hld.md`` §7 and ``fresh_linkedin_lld.md`` §2):
    - ``name == "fresh_linkedin"``
    - ``supported_operations == ["enrich_person"]``
    - ``cacheable == False`` (D17)
    - ``retry_config`` set to 1 retry after 60s, no jitter (D22)
    - Unsupported operation → ``ProviderError(400)``
    - Missing ``linkedin_url`` → ``ProviderError(400)``
    - Malformed / wrong-path LinkedIn URL → ``ProviderError(400)``
"""

from __future__ import annotations

import pytest

from server.core.exceptions import ProviderError
from server.execution.providers.fresh_linkedin import FreshLinkedInProvider


# asyncio_mode=auto in pyproject.toml auto-marks async tests; no module-level
# pytestmark needed (and applying one globally would warn on sync tests).


# ---------------------------------------------------------------------------
# Class-level attributes — the contract with the orchestration layer
# ---------------------------------------------------------------------------


class TestClassContract:
    """``execute_single`` and the catalog rely on these class attributes."""

    def test_name_is_fresh_linkedin(self) -> None:
        assert FreshLinkedInProvider.name == "fresh_linkedin"

    def test_enrich_person_remains_supported(self) -> None:
        """V1 shipped ``enrich_person`` only; Phase 2 added the company +
        posts families. Pin that ``enrich_person`` stays supported so a future
        refactor doesn't silently drop the V1 entry point."""
        assert "enrich_person" in FreshLinkedInProvider.supported_operations

    def test_is_not_cacheable(self) -> None:
        assert FreshLinkedInProvider.cacheable is False

    def test_has_custom_retry_config(self) -> None:
        cfg = FreshLinkedInProvider.retry_config
        assert cfg is not None, "fresh_linkedin must override the default retry policy (D22)"
        assert cfg.max_retries == 1, "D22: one retry, then surface error"
        assert cfg.base_delay == 60.0, "D22: ~60s wait before retry"
        assert cfg.jitter is False, "D22: deterministic 60s wait, not jittered"


# ---------------------------------------------------------------------------
# Entry-gate validation — rejects bad inputs before any HTTP happens
# ---------------------------------------------------------------------------


class TestRejectsUnsupportedOperations:
    """The orchestration layer also guards this, but the provider must
    still refuse unsupported operations defensively."""

    @pytest.mark.parametrize(
        "operation",
        ["search_people", "search_companies", "bogus_op", ""],
    )
    async def test_raises_400(self, operation: str) -> None:
        provider = FreshLinkedInProvider()

        with pytest.raises(ProviderError) as exc_info:
            await provider.execute(
                operation=operation,
                params={"linkedin_url": "https://www.linkedin.com/in/janedoe"},
                api_key="fake-key",
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.provider == "fresh_linkedin"


class TestRejectsMissingLinkedInUrl:
    """``linkedin_url`` is the single required input for V1."""

    @pytest.mark.parametrize(
        "params",
        [
            {},
            {"email": "jane@acme.com"},
            {"name": "Jane Doe", "company": "Acme"},
            {"linkedin_url": None},
            {"linkedin_url": ""},
            {"linkedin_url": "   "},
        ],
    )
    async def test_raises_400(self, params: dict) -> None:
        provider = FreshLinkedInProvider()

        with pytest.raises(ProviderError) as exc_info:
            await provider.execute(
                operation="enrich_person",
                params=params,
                api_key="fake-key",
            )

        assert exc_info.value.status_code == 400
        assert "linkedin_url" in str(exc_info.value).lower()


class TestRejectsMalformedLinkedInUrls:
    """Company / job / post / non-LinkedIn URLs are rejected at the gate —
    the normaliser raises, the provider surfaces it as a clean 400."""

    @pytest.mark.parametrize(
        "bad_url",
        [
            "https://www.linkedin.com/company/acme",
            "https://www.linkedin.com/jobs/view/12345",
            "https://www.linkedin.com/posts/janedoe-abc123",
            "https://twitter.com/janedoe",
            "not-a-url",
            "https://www.linkedin.com/",
        ],
    )
    async def test_raises_400(self, bad_url: str) -> None:
        provider = FreshLinkedInProvider()

        with pytest.raises(ProviderError) as exc_info:
            await provider.execute(
                operation="enrich_person",
                params={"linkedin_url": bad_url},
                api_key="fake-key",
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.provider == "fresh_linkedin"


class TestValidationFailuresDoNotRequireApiKey:
    """The 400 gate must fire before any key-sensitive operation runs.
    An empty api_key should not change the error — the validation error
    always comes first."""

    async def test_missing_url_with_empty_key(self) -> None:
        provider = FreshLinkedInProvider()

        with pytest.raises(ProviderError) as exc_info:
            await provider.execute(
                operation="enrich_person",
                params={},
                api_key="",
            )

        assert exc_info.value.status_code == 400
        assert "linkedin_url" in str(exc_info.value).lower()
