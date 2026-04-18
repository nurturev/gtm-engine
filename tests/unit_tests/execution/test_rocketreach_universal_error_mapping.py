"""Unit tests for the Universal-Credits 403 mapping in
``RocketReachProvider.execute``.

Requirements §5.3 + LLD §3.4 T4 + §4 Error-handling matrix.

The Universal endpoints return ``403 {"detail": "These endpoints require
Universal Credits."}`` when the caller's key has no Universal allocation.
The provider must map that distinct vendor-side 403 to **HTTP 402 Payment
Required** so the router and the agent can distinguish it from a generic
403 and react differently (plan upgrade path vs permission issue).

Scope: provider-level status-code mapping. Executes the real
``RocketReachProvider.execute`` end-to-end against an ``httpx.MockTransport``
— no internal collaborators are mocked.
"""

from __future__ import annotations

import httpx
import pytest

from server.core.exceptions import ProviderError
from server.execution.providers import rocketreach as rr_module
from server.execution.providers.rocketreach import RocketReachProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _install_fake_httpx(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    class _FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(rr_module.httpx, "AsyncClient", _FakeAsyncClient)


VALID_ENRICH_PERSON_PARAMS = {"linkedin_url": "https://linkedin.com/in/jane"}


# ---------------------------------------------------------------------------
# Distinct 402 for Universal-Credits 403
# ---------------------------------------------------------------------------


class TestUniversalCreditsFailureMapsTo402:
    """Requirements §5.3 + LLD §4: a 403 with a body mentioning Universal
    Credits is a plan-level failure and must surface as ``status_code=402``
    with an unambiguous upgrade message."""

    async def test_status_code_is_402(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={
                "detail": "These endpoints require Universal Credits.",
            })

        _install_fake_httpx(monkeypatch, handler)

        with pytest.raises(ProviderError) as exc_info:
            await RocketReachProvider().execute(
                operation="enrich_person",
                params=VALID_ENRICH_PERSON_PARAMS,
                api_key="fake-key",
            )

        assert exc_info.value.status_code == 402

    async def test_error_message_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An agent reading this error should know it's a plan problem, not
        a transient one, and know where to go. We pin the presence of
        'upgrade' + 'RocketReach' — exact copy can evolve."""
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={
                "detail": "These endpoints require Universal Credits.",
            })

        _install_fake_httpx(monkeypatch, handler)

        with pytest.raises(ProviderError) as exc_info:
            await RocketReachProvider().execute(
                operation="enrich_person",
                params=VALID_ENRICH_PERSON_PARAMS,
                api_key="fake-key",
            )

        msg = str(exc_info.value).lower()
        assert "rocketreach" in msg
        assert "upgrade" in msg or "universal" in msg

    async def test_provider_name_is_preserved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={
                "detail": "These endpoints require Universal Credits.",
            })

        _install_fake_httpx(monkeypatch, handler)

        with pytest.raises(ProviderError) as exc_info:
            await RocketReachProvider().execute(
                operation="enrich_person",
                params=VALID_ENRICH_PERSON_PARAMS,
                api_key="fake-key",
            )

        assert exc_info.value.provider == "rocketreach"


# ---------------------------------------------------------------------------
# Generic 403 stays a 403
# ---------------------------------------------------------------------------


class TestGenericForbiddenStaysAs403:
    """A vendor 403 that doesn't mention Universal Credits is a plan /
    permission problem we don't translate — keep the existing 403 surface
    so the retry layer still treats it as non-retryable."""

    @pytest.mark.parametrize(
        "body",
        [
            {"detail": "Plan does not include this feature."},
            {"detail": "Access denied."},
            {"error": "Forbidden"},
            {},
        ],
    )
    async def test_generic_forbidden_stays_403(
        self, monkeypatch: pytest.MonkeyPatch, body: dict
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json=body)

        _install_fake_httpx(monkeypatch, handler)

        with pytest.raises(ProviderError) as exc_info:
            await RocketReachProvider().execute(
                operation="enrich_person",
                params=VALID_ENRICH_PERSON_PARAMS,
                api_key="fake-key",
            )

        assert exc_info.value.status_code == 403

    async def test_non_json_403_body_stays_403(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the vendor ships HTML on 403 (edge case), mapping must still
        fall back to the generic 403 branch — we must not blow up parsing
        the body."""
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="<html>forbidden</html>")

        _install_fake_httpx(monkeypatch, handler)

        with pytest.raises(ProviderError) as exc_info:
            await RocketReachProvider().execute(
                operation="enrich_person",
                params=VALID_ENRICH_PERSON_PARAMS,
                api_key="fake-key",
            )

        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Other error codes unchanged by the migration
# ---------------------------------------------------------------------------


class TestOtherErrorCodesUnchangedByMigration:
    async def test_401_surfaces_as_provider_error_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "invalid key"})

        _install_fake_httpx(monkeypatch, handler)

        with pytest.raises(ProviderError) as exc_info:
            await RocketReachProvider().execute(
                operation="enrich_person",
                params=VALID_ENRICH_PERSON_PARAMS,
                api_key="fake-key",
            )

        assert exc_info.value.status_code == 401

    async def test_429_surfaces_as_provider_error_429(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "10"})

        _install_fake_httpx(monkeypatch, handler)

        with pytest.raises(ProviderError) as exc_info:
            await RocketReachProvider().execute(
                operation="enrich_person",
                params=VALID_ENRICH_PERSON_PARAMS,
                api_key="fake-key",
            )

        assert exc_info.value.status_code == 429

    async def test_404_returns_match_not_found_sentinel_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """404 is a confirmed upstream miss, not an error. The provider
        contract surfaces a no-match sentinel so the normalizer can shape
        it consistently."""
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "not found"})

        _install_fake_httpx(monkeypatch, handler)

        result = await RocketReachProvider().execute(
            operation="enrich_person",
            params=VALID_ENRICH_PERSON_PARAMS,
            api_key="fake-key",
        )

        assert result == {"match_found": False, "profiles": []}

    async def test_500_surfaces_as_retryable_5xx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal server error")

        _install_fake_httpx(monkeypatch, handler)

        with pytest.raises(ProviderError) as exc_info:
            await RocketReachProvider().execute(
                operation="enrich_person",
                params=VALID_ENRICH_PERSON_PARAMS,
                api_key="fake-key",
            )

        assert exc_info.value.status_code == 500
