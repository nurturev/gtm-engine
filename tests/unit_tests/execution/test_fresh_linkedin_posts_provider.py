"""Phase 2.2 + 2.3 — Post-family provider dispatch & validation.

Covers: ``fetch_profile_posts``, ``fetch_company_posts``, ``fetch_post_details``,
``fetch_post_reactions``, ``fetch_post_comments``. All five share:
- The URN validator (for the three URN-keyed ops).
- The URL type validators (profile vs company).
- Dispatch-by-operation semantics through ``FreshLinkedInProvider.execute``.

Wire-level HTTP happy-path tests live alongside the normalizer tests in
``test_fresh_linkedin_post_normalizer.py`` — this file focuses on the
behaviour of the ``execute`` gate + dispatcher only.

Search (O8, Phase 2.4) is covered in its own file because the POST-body
payload shape is specific to it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from server.core.exceptions import ProviderError
from server.execution.providers.fresh_linkedin import FreshLinkedInProvider


FAKE_KEY = "fake-rapidapi-key"


_POST_OPS = {
    "fetch_profile_posts", "fetch_company_posts",
    "fetch_post_details", "fetch_post_reactions", "fetch_post_comments",
}

pytestmark = pytest.mark.skipif(
    not _POST_OPS.issubset(set(FreshLinkedInProvider.supported_operations)),
    reason="Phase 2.2/2.3 not yet implemented — post-family ops not registered",
)


def _mock_response(status_code: int = 200, json_body: dict | None = None):
    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json = lambda: (json_body or {"data": [], "total": 0})
    mock_resp.text = ""
    return mock_resp


# ---------------------------------------------------------------------------
# Class-level contract
# ---------------------------------------------------------------------------


class TestSupportedOperationsIncludesPostFamily:
    """The catalog / orchestration layer reads ``supported_operations``;
    if any op is missing, the router rejects it before we ever dispatch."""

    @pytest.mark.parametrize(
        "op",
        [
            "fetch_profile_posts",
            "fetch_company_posts",
            "fetch_post_details",
            "fetch_post_reactions",
            "fetch_post_comments",
        ],
    )
    def test_op_listed(self, op: str) -> None:
        assert op in FreshLinkedInProvider.supported_operations


# ---------------------------------------------------------------------------
# URN-keyed op validation — bare activity id required
# ---------------------------------------------------------------------------


class TestUrnValidationGatesUrnKeyedOps:
    """The validator is shared across O5/O6/O7 — prove the gate fires for each."""

    @pytest.mark.parametrize(
        "op", ["fetch_post_details", "fetch_post_reactions", "fetch_post_comments"],
    )
    @pytest.mark.parametrize(
        "bad_urn",
        [
            "urn:li:activity:7450415215956987904",
            "https://www.linkedin.com/feed/update/urn:li:activity:7450415215956987904/",
            "abc",
            "",
            None,
        ],
    )
    async def test_rejects_non_bare_urn_before_http(
        self, op: str, bad_urn,
    ) -> None:
        provider = FreshLinkedInProvider()

        # No ``patch`` here — if the validator fires before HTTP (as it must),
        # we never attempt a network call. If the validator leaks, this test
        # will timeout or error rather than raise ProviderError — both are
        # failure modes the maintainer wants to see.
        with pytest.raises(ProviderError) as exc_info:
            await provider.execute(
                operation=op,
                params={"urn": bad_urn},
                api_key=FAKE_KEY,
            )

        assert exc_info.value.status_code == 400

    @pytest.mark.parametrize(
        "op", ["fetch_post_details", "fetch_post_reactions", "fetch_post_comments"],
    )
    async def test_missing_urn_key_raises_400(self, op: str) -> None:
        provider = FreshLinkedInProvider()

        with pytest.raises(ProviderError) as exc_info:
            await provider.execute(
                operation=op,
                params={},
                api_key=FAKE_KEY,
            )
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# URL-type validation for fetch_profile_posts vs fetch_company_posts
# ---------------------------------------------------------------------------


class TestProfilePostsRejectsCompanyUrl:
    async def test_company_url_raises_400(self) -> None:
        provider = FreshLinkedInProvider()

        with pytest.raises(ProviderError) as exc_info:
            await provider.execute(
                operation="fetch_profile_posts",
                params={"linkedin_url": "https://www.linkedin.com/company/google/"},
                api_key=FAKE_KEY,
            )
        assert exc_info.value.status_code == 400


class TestCompanyPostsRejectsProfileUrl:
    async def test_profile_url_raises_400(self) -> None:
        provider = FreshLinkedInProvider()

        with pytest.raises(ProviderError) as exc_info:
            await provider.execute(
                operation="fetch_company_posts",
                params={"linkedin_url": "https://www.linkedin.com/in/mohnishkewlani/"},
                api_key=FAKE_KEY,
            )
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Dispatch — each op hits its specific upstream endpoint
# ---------------------------------------------------------------------------


class TestDispatchHitsCorrectUpstreamPath:
    @pytest.mark.parametrize(
        "op,params,expected_path",
        [
            (
                "fetch_profile_posts",
                {"linkedin_url": "https://www.linkedin.com/in/mohnishkewlani"},
                "/get-profile-posts",
            ),
            (
                "fetch_company_posts",
                {"linkedin_url": "https://www.linkedin.com/company/google"},
                "/get-company-posts",
            ),
            (
                "fetch_post_details",
                {"urn": "7450415215956987904"},
                "/get-post-details",
            ),
            (
                "fetch_post_reactions",
                {"urn": "7450415215956987904"},
                "/get-post-reactions",
            ),
            (
                "fetch_post_comments",
                {"urn": "7450415215956987904"},
                "/get-post-comments",
            ),
        ],
    )
    async def test_dispatch(self, op: str, params: dict, expected_path: str) -> None:
        provider = FreshLinkedInProvider()

        with patch(
            "httpx.AsyncClient.get", new=AsyncMock(return_value=_mock_response()),
        ) as mock_get:
            await provider.execute(operation=op, params=params, api_key=FAKE_KEY)

        hit_url = (
            mock_get.await_args.args[0]
            if mock_get.await_args.args
            else mock_get.await_args.kwargs.get("url", "")
        )
        assert expected_path in hit_url


# ---------------------------------------------------------------------------
# Pagination pass-through — caller drives, provider relays
# ---------------------------------------------------------------------------


class TestCursorIsPassedUpstream:
    """Cursors for profile_posts / company_posts / post_reactions are
    caller-orchestrated. The provider forwards whatever value the caller
    gives without interpreting it."""

    async def test_cursor_forwarded_to_upstream_params(self) -> None:
        provider = FreshLinkedInProvider()

        with patch(
            "httpx.AsyncClient.get", new=AsyncMock(return_value=_mock_response()),
        ) as mock_get:
            await provider.execute(
                operation="fetch_post_reactions",
                params={"urn": "7450415215956987904", "cursor": "abc123"},
                api_key=FAKE_KEY,
            )

        sent_params = mock_get.await_args.kwargs.get("params") or {}
        # Exact param name depends on vendor (cursor / pagination_token /
        # page_token); caller-facing key is ``cursor``. Just assert it got
        # forwarded — the value must appear *somewhere* in the params.
        assert any(v == "abc123" for v in sent_params.values()), (
            f"cursor value not forwarded upstream; sent params: {sent_params}"
        )


class TestCommentsPaginationTokenPassThrough:
    """Comments endpoint uses ``pagination_token`` natively. Caller can pass
    either ``cursor`` or ``pagination_token``; either must end up upstream."""

    async def test_pagination_token_forwarded(self) -> None:
        provider = FreshLinkedInProvider()

        with patch(
            "httpx.AsyncClient.get", new=AsyncMock(return_value=_mock_response()),
        ) as mock_get:
            await provider.execute(
                operation="fetch_post_comments",
                params={"urn": "7450415215956987904",
                        "pagination_token": "tok-42"},
                api_key=FAKE_KEY,
            )

        sent_params = mock_get.await_args.kwargs.get("params") or {}
        assert any(v == "tok-42" for v in sent_params.values())
