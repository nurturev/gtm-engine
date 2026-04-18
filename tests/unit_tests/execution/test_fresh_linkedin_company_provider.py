"""Phase 2.1 — ``enrich_company`` provider tests.

Scope: entry-gate validation + endpoint dispatch for ``enrich_company`` on
``provider="fresh_linkedin"``. No HTTP happens on validation paths — the
fail-fast gates run before any network call.

For the dispatch tests we patch ``httpx.AsyncClient.get`` and assert on the
URL and params the provider tried to hit. (One mock per test; ≤3 per file.)

Contract (HLD Phase 2.1 §3, §6, §7):
- Dispatch: ``linkedin_url`` → ``/get-company-by-linkedinurl``.
- Dispatch: ``domain`` → ``/get-company-by-domain``.
- Both given → ``linkedin_url`` wins (documented precedence).
- Neither given → ``ProviderError(400)``.
- Profile URL in ``linkedin_url`` → ``ProviderError(400)`` with clear message.
- Malformed ``domain`` → ``ProviderError(400)``.
- ``enrich_company`` listed in ``supported_operations``.
- Upstream 404 → ``{match_found: False, data: None}`` (no error, credit still debited).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from server.core.exceptions import ProviderError
from server.execution.providers.fresh_linkedin import FreshLinkedInProvider


FAKE_KEY = "fake-rapidapi-key"


pytestmark = pytest.mark.skipif(
    "enrich_company" not in FreshLinkedInProvider.supported_operations,
    reason="Phase 2.1 not yet implemented — enrich_company op not registered",
)


def _build_mock_response(
    *,
    status_code: int = 200,
    json_body: dict | None = None,
) -> AsyncMock:
    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json = lambda: (json_body or {})
    mock_resp.text = ""
    return mock_resp


# ---------------------------------------------------------------------------
# Class-level contract
# ---------------------------------------------------------------------------


class TestEnrichCompanyRegisteredAsSupportedOperation:
    def test_listed_in_supported_operations(self) -> None:
        assert "enrich_company" in FreshLinkedInProvider.supported_operations


# ---------------------------------------------------------------------------
# Entry-gate validation — no HTTP
# ---------------------------------------------------------------------------


class TestRejectsMissingInputs:
    @pytest.mark.parametrize(
        "params",
        [
            {},
            {"linkedin_url": None},
            {"linkedin_url": ""},
            {"domain": ""},
            {"domain": None},
            {"linkedin_url": None, "domain": None},
        ],
    )
    async def test_empty_or_missing_raises_400(self, params: dict) -> None:
        provider = FreshLinkedInProvider()

        with pytest.raises(ProviderError) as exc_info:
            await provider.execute(
                operation="enrich_company",
                params=params,
                api_key=FAKE_KEY,
            )

        assert exc_info.value.status_code == 400
        msg = str(exc_info.value).lower()
        # Message must point to the two acceptable inputs.
        assert "linkedin_url" in msg or "domain" in msg


class TestRejectsProfileUrlAsCompanyUrl:
    """The single most likely caller mistake — company op, profile URL.
    The 400 must name the problem so the user can self-correct."""

    @pytest.mark.parametrize(
        "bad_url",
        [
            "https://www.linkedin.com/in/janedoe",
            "https://linkedin.com/in/mohnishkewlani",
            "https://www.linkedin.com/in/janedoe/?utm=x",
        ],
    )
    async def test_raises_400_with_helpful_message(self, bad_url: str) -> None:
        provider = FreshLinkedInProvider()

        with pytest.raises(ProviderError) as exc_info:
            await provider.execute(
                operation="enrich_company",
                params={"linkedin_url": bad_url},
                api_key=FAKE_KEY,
            )

        assert exc_info.value.status_code == 400
        msg = str(exc_info.value).lower()
        # Caller needs to learn this is a company/profile mix-up.
        assert "profile" in msg or "/in/" in msg or "company" in msg


class TestRejectsMalformedDomain:
    @pytest.mark.parametrize("bad_domain", ["not a domain", "   ", "/path/only", "localhost"])
    async def test_raises_400(self, bad_domain: str) -> None:
        provider = FreshLinkedInProvider()

        with pytest.raises(ProviderError) as exc_info:
            await provider.execute(
                operation="enrich_company",
                params={"domain": bad_domain},
                api_key=FAKE_KEY,
            )

        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Endpoint dispatch — the core behaviour
# ---------------------------------------------------------------------------


class TestDispatchToUpstreamEndpoint:
    """The provider picks the correct upstream endpoint based on which input is
    present. Mock the HTTP client and assert on the URL + params we sent."""

    async def test_linkedin_url_routes_to_company_by_linkedinurl(self) -> None:
        provider = FreshLinkedInProvider()
        mock_resp = _build_mock_response(json_body={"data": {"company_name": "Google"}})

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)) as mock_get:
            await provider.execute(
                operation="enrich_company",
                params={"linkedin_url": "https://www.linkedin.com/company/google"},
                api_key=FAKE_KEY,
            )

        call_args = mock_get.await_args
        hit_url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert "/get-company-by-linkedinurl" in hit_url
        params = call_args.kwargs.get("params") or {}
        # Canonical URL was sent (with trailing slash or not — don't over-specify).
        assert "linkedin_url" in params
        assert "linkedin.com/company/google" in params["linkedin_url"]

    async def test_domain_routes_to_company_by_domain(self) -> None:
        provider = FreshLinkedInProvider()
        mock_resp = _build_mock_response(json_body={"data": {"company_name": "Acme"}})

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)) as mock_get:
            await provider.execute(
                operation="enrich_company",
                params={"domain": "https://www.acme.com/about"},
                api_key=FAKE_KEY,
            )

        call_args = mock_get.await_args
        hit_url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert "/get-company-by-domain" in hit_url
        params = call_args.kwargs.get("params") or {}
        # Domain auto-stripped before the upstream call.
        assert params.get("domain") == "acme.com"

    async def test_both_inputs_present_linkedin_url_wins(self) -> None:
        """Documented precedence (HLD §3.1): when both present, URL wins."""
        provider = FreshLinkedInProvider()
        mock_resp = _build_mock_response(json_body={"data": {"company_name": "Google"}})

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)) as mock_get:
            await provider.execute(
                operation="enrich_company",
                params={
                    "linkedin_url": "https://www.linkedin.com/company/google",
                    "domain": "acme.com",
                },
                api_key=FAKE_KEY,
            )

        hit_url = mock_get.await_args.args[0] if mock_get.await_args.args \
            else mock_get.await_args.kwargs.get("url", "")
        assert "/get-company-by-linkedinurl" in hit_url
        assert "/get-company-by-domain" not in hit_url


# ---------------------------------------------------------------------------
# Upstream status-code handling — mirrors enrich_person conventions
# ---------------------------------------------------------------------------


class TestUpstream404IsNotAnError:
    """Platform convention: upstream 404 → ``match_found: False`` sentinel,
    credit still debited (HLD §9 failure modes). This gets normalized into
    an empty row by the normalizer, not surfaced as an exception."""

    async def test_returns_match_found_false(self) -> None:
        provider = FreshLinkedInProvider()
        mock_resp = _build_mock_response(status_code=404, json_body={})

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            result = await provider.execute(
                operation="enrich_company",
                params={"linkedin_url": "https://www.linkedin.com/company/unknown"},
                api_key=FAKE_KEY,
            )

        assert result.get("match_found") is False


class TestUpstream429BubblesAsRateLimit:
    async def test_429_raises_provider_error_with_correct_status(self) -> None:
        provider = FreshLinkedInProvider()
        mock_resp = _build_mock_response(status_code=429, json_body={})

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            with pytest.raises(ProviderError) as exc_info:
                await provider.execute(
                    operation="enrich_company",
                    params={"linkedin_url": "https://www.linkedin.com/company/google"},
                    api_key=FAKE_KEY,
                )

        assert exc_info.value.status_code == 429
