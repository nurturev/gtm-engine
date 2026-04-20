"""Post-family provider dispatch + per-op pagination plumbing.

Covers:
- Entry-gate URN + URL validation (Phase 2.2 / 2.3).
- Dispatch-by-operation semantics through ``FreshLinkedInProvider.execute``.
- Per-op pagination parameter wiring introduced in Phase 2.5 (vendor-native
  names — no unified ``cursor`` abstraction). See HLD §5, LLD §4.1.

Pagination model per op (HLD §5):
    fetch_profile_posts  → start + pagination_token (pair rule)  + type filter
    fetch_company_posts  → start + pagination_token (pair rule)  + sort_by
    fetch_post_comments  → page  + pagination_token (pair rule)  + sort_by
    fetch_post_reactions → page (single value, no pair)          + type filter

Wire-level HTTP happy-path tests live alongside the normalizer tests.
Search (Phase 2.4) lives in its own file because its POST-body shape differs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from server.core.exceptions import ProviderError
from server.execution.providers.fresh_linkedin import FreshLinkedInProvider


FAKE_KEY = "fake-rapidapi-key"
VALID_PROFILE_URL = "https://www.linkedin.com/in/mohnishkewlani"
VALID_COMPANY_URL = "https://www.linkedin.com/company/google"
VALID_URN = "7450415215956987904"


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


async def _capture_get(op: str, params: dict) -> dict:
    """Execute ``op`` with ``params`` against a patched ``httpx.AsyncClient.get``
    and return the exact query-string dict the provider sent upstream."""
    provider = FreshLinkedInProvider()
    with patch(
        "httpx.AsyncClient.get", new=AsyncMock(return_value=_mock_response()),
    ) as mock_get:
        await provider.execute(operation=op, params=params, api_key=FAKE_KEY)
    return mock_get.await_args.kwargs.get("params") or {}


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
                {"linkedin_url": VALID_PROFILE_URL},
                "/get-profile-posts",
            ),
            (
                "fetch_company_posts",
                {"linkedin_url": VALID_COMPANY_URL},
                "/get-company-posts",
            ),
            (
                "fetch_post_details",
                {"urn": VALID_URN},
                "/get-post-details",
            ),
            (
                "fetch_post_reactions",
                {"urn": VALID_URN},
                "/get-post-reactions",
            ),
            (
                "fetch_post_comments",
                {"urn": VALID_URN},
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
# fetch_profile_posts — pagination + filter wiring (HLD §5.1)
# ---------------------------------------------------------------------------


class TestProfilePostsFirstCallCarriesOnlyUrl:
    """No pagination params on page 1 — upstream query string is just the URL."""

    async def test_qs_is_just_linkedin_url(self) -> None:
        qs = await _capture_get(
            "fetch_profile_posts", {"linkedin_url": VALID_PROFILE_URL},
        )
        assert "linkedin_url" in qs
        assert "start" not in qs
        assert "pagination_token" not in qs


class TestProfilePostsPaginationRoundTripsVerbatim:
    """Given both pagination values, they reach upstream under vendor-native
    keys without transformation."""

    async def test_start_and_token_sent_together(self) -> None:
        qs = await _capture_get(
            "fetch_profile_posts",
            {
                "linkedin_url": VALID_PROFILE_URL,
                "start": "10",
                "pagination_token": "tok1",
            },
        )
        assert qs.get("start") == "10"
        assert qs.get("pagination_token") == "tok1"

    async def test_int_start_coerced_to_string(self) -> None:
        """Claude / Python callers commonly send ``start`` as ``int``; the
        provider must coerce to the vendor's string shape."""
        qs = await _capture_get(
            "fetch_profile_posts",
            {
                "linkedin_url": VALID_PROFILE_URL,
                "start": 10,
                "pagination_token": "tok1",
            },
        )
        assert qs.get("start") == "10"


class TestProfilePostsPairingRule:
    """HLD §5.1 — ``start`` and ``pagination_token`` must be sent together or
    both omitted. Solo values raise 400 *before* any upstream call."""

    async def test_start_alone_raises_400_no_http(self) -> None:
        provider = FreshLinkedInProvider()
        with patch(
            "httpx.AsyncClient.get", new=AsyncMock(return_value=_mock_response()),
        ) as mock_get:
            with pytest.raises(ProviderError) as exc_info:
                await provider.execute(
                    operation="fetch_profile_posts",
                    params={
                        "linkedin_url": VALID_PROFILE_URL,
                        "start": "10",
                    },
                    api_key=FAKE_KEY,
                )
        assert exc_info.value.status_code == 400
        assert mock_get.await_count == 0

    async def test_token_alone_raises_400_no_http(self) -> None:
        provider = FreshLinkedInProvider()
        with patch(
            "httpx.AsyncClient.get", new=AsyncMock(return_value=_mock_response()),
        ) as mock_get:
            with pytest.raises(ProviderError) as exc_info:
                await provider.execute(
                    operation="fetch_profile_posts",
                    params={
                        "linkedin_url": VALID_PROFILE_URL,
                        "pagination_token": "tok1",
                    },
                    api_key=FAKE_KEY,
                )
        assert exc_info.value.status_code == 400
        assert mock_get.await_count == 0


class TestProfilePostsTypeFilterPassedThrough:
    async def test_type_filter_in_qs(self) -> None:
        qs = await _capture_get(
            "fetch_profile_posts",
            {"linkedin_url": VALID_PROFILE_URL, "type": "posts"},
        )
        assert qs.get("type") == "posts"


class TestProfilePostsLegacyCursorSilentlyDropped:
    """HLD §5.7 — ``cursor`` is an unknown param on this op; silently dropped,
    never forwarded to the vendor, and never 400s."""

    async def test_cursor_not_sent_upstream(self) -> None:
        qs = await _capture_get(
            "fetch_profile_posts",
            {"linkedin_url": VALID_PROFILE_URL, "cursor": "abc123"},
        )
        assert "cursor" not in qs
        assert "abc123" not in qs.values()


# ---------------------------------------------------------------------------
# fetch_company_posts — same pairing, sort_by filter (HLD §5.2)
# ---------------------------------------------------------------------------


class TestCompanyPostsPaginationRoundTrip:
    async def test_start_and_token_sent_together(self) -> None:
        qs = await _capture_get(
            "fetch_company_posts",
            {
                "linkedin_url": VALID_COMPANY_URL,
                "start": "10",
                "pagination_token": "tok2",
            },
        )
        assert qs.get("start") == "10"
        assert qs.get("pagination_token") == "tok2"


class TestCompanyPostsPairingRule:
    async def test_start_alone_raises_400_no_http(self) -> None:
        provider = FreshLinkedInProvider()
        with patch(
            "httpx.AsyncClient.get", new=AsyncMock(return_value=_mock_response()),
        ) as mock_get:
            with pytest.raises(ProviderError) as exc_info:
                await provider.execute(
                    operation="fetch_company_posts",
                    params={"linkedin_url": VALID_COMPANY_URL, "start": "10"},
                    api_key=FAKE_KEY,
                )
        assert exc_info.value.status_code == 400
        assert mock_get.await_count == 0

    async def test_token_alone_raises_400_no_http(self) -> None:
        provider = FreshLinkedInProvider()
        with patch(
            "httpx.AsyncClient.get", new=AsyncMock(return_value=_mock_response()),
        ) as mock_get:
            with pytest.raises(ProviderError) as exc_info:
                await provider.execute(
                    operation="fetch_company_posts",
                    params={"linkedin_url": VALID_COMPANY_URL, "pagination_token": "tok2"},
                    api_key=FAKE_KEY,
                )
        assert exc_info.value.status_code == 400
        assert mock_get.await_count == 0


class TestCompanyPostsSortByFilterPassedThrough:
    async def test_sort_by_in_qs(self) -> None:
        qs = await _capture_get(
            "fetch_company_posts",
            {"linkedin_url": VALID_COMPANY_URL, "sort_by": "top"},
        )
        assert qs.get("sort_by") == "top"


class TestCompanyPostsLegacyCursorSilentlyDropped:
    async def test_cursor_not_sent_upstream(self) -> None:
        qs = await _capture_get(
            "fetch_company_posts",
            {"linkedin_url": VALID_COMPANY_URL, "cursor": "abc"},
        )
        assert "cursor" not in qs


# ---------------------------------------------------------------------------
# fetch_post_comments — page + pagination_token pairing, sort_by (HLD §5.3)
# ---------------------------------------------------------------------------


class TestCommentsFirstCallCarriesOnlyUrn:
    async def test_qs_is_just_urn(self) -> None:
        qs = await _capture_get("fetch_post_comments", {"urn": VALID_URN})
        assert qs.get("urn") == VALID_URN
        assert "page" not in qs
        assert "pagination_token" not in qs


class TestCommentsPaginationRoundTrip:
    async def test_page_and_token_sent_together(self) -> None:
        qs = await _capture_get(
            "fetch_post_comments",
            {"urn": VALID_URN, "page": "2", "pagination_token": "tok-42"},
        )
        assert qs.get("page") == "2"
        assert qs.get("pagination_token") == "tok-42"

    async def test_int_page_coerced_to_string(self) -> None:
        qs = await _capture_get(
            "fetch_post_comments",
            {"urn": VALID_URN, "page": 2, "pagination_token": "tok-42"},
        )
        assert qs.get("page") == "2"


class TestCommentsPairingRule:
    async def test_page_alone_raises_400_no_http(self) -> None:
        provider = FreshLinkedInProvider()
        with patch(
            "httpx.AsyncClient.get", new=AsyncMock(return_value=_mock_response()),
        ) as mock_get:
            with pytest.raises(ProviderError) as exc_info:
                await provider.execute(
                    operation="fetch_post_comments",
                    params={"urn": VALID_URN, "page": "2"},
                    api_key=FAKE_KEY,
                )
        assert exc_info.value.status_code == 400
        assert mock_get.await_count == 0

    async def test_token_alone_raises_400_no_http(self) -> None:
        provider = FreshLinkedInProvider()
        with patch(
            "httpx.AsyncClient.get", new=AsyncMock(return_value=_mock_response()),
        ) as mock_get:
            with pytest.raises(ProviderError) as exc_info:
                await provider.execute(
                    operation="fetch_post_comments",
                    params={"urn": VALID_URN, "pagination_token": "tok-42"},
                    api_key=FAKE_KEY,
                )
        assert exc_info.value.status_code == 400
        assert mock_get.await_count == 0


class TestCommentsSortByFilterPassedThrough:
    async def test_sort_by_in_qs(self) -> None:
        qs = await _capture_get(
            "fetch_post_comments",
            {"urn": VALID_URN, "sort_by": "Most relevant"},
        )
        assert qs.get("sort_by") == "Most relevant"


class TestCommentsLegacyCursorAliasNoLongerMapsToToken:
    """Pre-Phase-2.5 the provider accepted ``cursor`` as an alias for
    ``pagination_token`` on this op. Post-phase it's silently dropped —
    callers must send ``pagination_token`` (and the paired ``page``)."""

    async def test_cursor_not_forwarded(self) -> None:
        qs = await _capture_get(
            "fetch_post_comments",
            {"urn": VALID_URN, "cursor": "legacy-tok"},
        )
        # Must NOT land under any vendor key.
        assert "cursor" not in qs
        assert "pagination_token" not in qs
        assert "legacy-tok" not in qs.values()


# ---------------------------------------------------------------------------
# fetch_post_reactions — single-value page, type filter (HLD §5.4)
# ---------------------------------------------------------------------------


class TestReactionsFirstCallCarriesOnlyUrn:
    async def test_qs_is_just_urn(self) -> None:
        qs = await _capture_get("fetch_post_reactions", {"urn": VALID_URN})
        assert qs.get("urn") == VALID_URN
        assert "page" not in qs


class TestReactionsPagePassedThrough:
    """Reactions is the only op with single-value pagination — no paired
    token, caller just bumps ``page``."""

    async def test_page_string_passed_through(self) -> None:
        qs = await _capture_get(
            "fetch_post_reactions",
            {"urn": VALID_URN, "page": "2"},
        )
        assert qs.get("page") == "2"

    async def test_int_page_coerced_to_string(self) -> None:
        qs = await _capture_get(
            "fetch_post_reactions",
            {"urn": VALID_URN, "page": 2},
        )
        assert qs.get("page") == "2"


class TestReactionsTypeFilterPassedThrough:
    async def test_type_filter_in_qs(self) -> None:
        qs = await _capture_get(
            "fetch_post_reactions",
            {"urn": VALID_URN, "type": "ALL"},
        )
        assert qs.get("type") == "ALL"


class TestReactionsRejectsInvalidPage:
    """Vendor-wire validation via ``_coerce_numeric_string`` happens at the
    provider — bad values 400 before any upstream call."""

    @pytest.mark.parametrize("bad_page", ["abc", -1, "1.5"])
    async def test_invalid_page_raises_400(self, bad_page) -> None:
        provider = FreshLinkedInProvider()
        with patch(
            "httpx.AsyncClient.get", new=AsyncMock(return_value=_mock_response()),
        ) as mock_get:
            with pytest.raises(ProviderError) as exc_info:
                await provider.execute(
                    operation="fetch_post_reactions",
                    params={"urn": VALID_URN, "page": bad_page},
                    api_key=FAKE_KEY,
                )
        assert exc_info.value.status_code == 400
        assert mock_get.await_count == 0


class TestReactionsPaginationTokenSilentlyDropped:
    """HLD §5.7 — ``pagination_token`` is not a vendor param for this endpoint;
    drop silently (consistent with other unknown-param handling), do NOT 400."""

    async def test_pagination_token_not_forwarded(self) -> None:
        qs = await _capture_get(
            "fetch_post_reactions",
            {"urn": VALID_URN, "pagination_token": "ignored"},
        )
        assert "pagination_token" not in qs
        assert "ignored" not in qs.values()

    async def test_cursor_legacy_alias_also_dropped(self) -> None:
        qs = await _capture_get(
            "fetch_post_reactions",
            {"urn": VALID_URN, "cursor": "legacy"},
        )
        assert "cursor" not in qs
        assert "legacy" not in qs.values()
