"""Phase 2.4 — ``search_posts`` provider tests.

Two concerns, tested separately:
1. **Payload builder + filter validation** — ``FreshLinkedInProvider._build_search_payload``
   is a pure method. Zero mocks.
2. **Dispatch to POST upstream** — first POST-bodied op in this provider;
   patch ``httpx.AsyncClient.post`` and assert on the JSON body.

Search result normalization shares the same ``normalize_post`` function
tested in ``test_fresh_linkedin_post_normalizer.py`` (coalescing rules).

Contract (HLD Phase 2.4 §3, §7):
- Caller only sends the filters they care about; provider merges with
  ``_SEARCH_DEFAULT_PAYLOAD`` so upstream always receives all 12 keys.
- Vendor requires empty strings (``""``) and empty lists (``[]``) — never
  ``null`` — for unused filter slots.
- At least one filter must be non-empty. All-empty body → 400, no upstream.
- ``page`` must be a positive integer; 0, negative, non-int → 400.
- Dispatch: ``search_posts`` → ``POST /search-posts`` with JSON body.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from server.core.exceptions import ProviderError
from server.execution.providers.fresh_linkedin import (
    _SEARCH_DEFAULT_PAYLOAD,
    _SEARCH_FILTER_LIST_KEYS,
    FreshLinkedInProvider,
)


FAKE_KEY = "fake-rapidapi-key"


# ---------------------------------------------------------------------------
# Default payload — the canonical-empty shape the vendor expects
# ---------------------------------------------------------------------------


class TestDefaultSearchPayloadHasAllTwelveFilterKeys:
    """Pin the canonical-empty shape the vendor expects. If a key is added
    or removed, callers who rely on a full-payload default will break."""

    EXPECTED_KEYS = frozenset({
        "search_keywords", "sort_by", "date_posted", "content_type",
        "from_member", "from_company",
        "mentioning_member", "mentioning_company",
        "author_company", "author_industry", "author_keyword",
        "page",
    })

    def test_default_payload_carries_all_expected_keys(self) -> None:
        assert set(_SEARCH_DEFAULT_PAYLOAD.keys()) == self.EXPECTED_KEYS

    def test_default_sort_by_is_latest(self) -> None:
        assert _SEARCH_DEFAULT_PAYLOAD["sort_by"] == "Latest"

    def test_default_page_is_one(self) -> None:
        assert _SEARCH_DEFAULT_PAYLOAD["page"] == 1

    def test_string_slots_default_to_empty_string_not_null(self) -> None:
        for key in ("search_keywords", "date_posted", "content_type", "author_keyword"):
            assert _SEARCH_DEFAULT_PAYLOAD[key] == "", (
                f"{key} must default to empty string, not None — vendor rejects null"
            )

    def test_list_slots_default_to_empty_list(self) -> None:
        for key in _SEARCH_FILTER_LIST_KEYS:
            assert _SEARCH_DEFAULT_PAYLOAD[key] == []


# ---------------------------------------------------------------------------
# _build_search_payload — merge + validate
# ---------------------------------------------------------------------------


class TestBuildSearchPayloadMerges:
    """Caller passes a partial dict; builder fills every missing slot with
    the canonical empty shape (so upstream sees all 12 keys)."""

    def test_caller_overrides_apply(self) -> None:
        provider = FreshLinkedInProvider()
        payload = provider._build_search_payload({"search_keywords": "GTM", "page": 2})
        assert payload["search_keywords"] == "GTM"
        assert payload["page"] == 2

    def test_unrelated_slots_fill_from_defaults(self) -> None:
        provider = FreshLinkedInProvider()
        payload = provider._build_search_payload({"search_keywords": "GTM"})
        assert payload["sort_by"] == "Latest"
        assert payload["date_posted"] == ""
        assert payload["from_member"] == []

    def test_payload_always_has_full_key_set(self) -> None:
        """Even with a single filter, upstream must receive every slot."""
        provider = FreshLinkedInProvider()
        payload = provider._build_search_payload({"search_keywords": "GTM"})
        assert set(payload.keys()) == set(_SEARCH_DEFAULT_PAYLOAD.keys())

    def test_unknown_caller_keys_ignored(self) -> None:
        """The builder must not forward unknown keys to the vendor — otherwise
        a typo like ``from_person`` would get sent upstream and 400."""
        provider = FreshLinkedInProvider()
        payload = provider._build_search_payload(
            {"from_person": ["ACoAA..."], "search_keywords": "GTM"},
        )
        assert "from_person" not in payload


class TestBuildSearchPayloadAutoWrapsStringListFilter:
    """Ergonomic affordance: caller may pass a single URN as a string rather
    than a one-element list. Builder wraps it into a list."""

    def test_string_urn_wrapped_as_list(self) -> None:
        provider = FreshLinkedInProvider()
        payload = provider._build_search_payload(
            {"from_member": "ACoAAA8BYqEBCGLg_vT_ca6mMEqkpp9nVffJ3hc"},
        )
        assert payload["from_member"] == ["ACoAAA8BYqEBCGLg_vT_ca6mMEqkpp9nVffJ3hc"]


class TestAtLeastOneFilterRule:
    def test_all_defaults_rejected(self) -> None:
        provider = FreshLinkedInProvider()
        with pytest.raises(ProviderError) as exc_info:
            provider._build_search_payload({})
        assert exc_info.value.status_code == 400

    def test_only_sort_by_populated_still_rejected(self) -> None:
        """``sort_by`` alone doesn't count as a filter (grooming §8.1 O8)."""
        provider = FreshLinkedInProvider()
        with pytest.raises(ProviderError):
            provider._build_search_payload({"sort_by": "Relevance"})

    @pytest.mark.parametrize(
        "param",
        [
            {"search_keywords": "GTM"},
            {"author_keyword": "founder"},
            {"from_member": ["ACoAA..."]},
            {"mentioning_company": ["123"]},
            {"author_company": ["456"]},
            {"author_industry": ["software-development"]},
        ],
    )
    def test_any_populated_filter_accepts(self, param: dict) -> None:
        provider = FreshLinkedInProvider()
        # Must not raise.
        provider._build_search_payload(param)

    def test_whitespace_only_keyword_does_not_count(self) -> None:
        provider = FreshLinkedInProvider()
        with pytest.raises(ProviderError):
            provider._build_search_payload({"search_keywords": "   "})


class TestPageValidation:
    @pytest.mark.parametrize("bad_page", [0, -1, 1.5, None])
    def test_invalid_page_raises_400(self, bad_page) -> None:
        provider = FreshLinkedInProvider()
        with pytest.raises(ProviderError) as exc_info:
            provider._build_search_payload(
                {"search_keywords": "GTM", "page": bad_page},
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.parametrize("good_page", [1, 2, 5, 100])
    def test_positive_int_accepted(self, good_page: int) -> None:
        provider = FreshLinkedInProvider()
        provider._build_search_payload(
            {"search_keywords": "GTM", "page": good_page},
        )


# ---------------------------------------------------------------------------
# Dispatch — hits POST /search-posts with JSON body
# ---------------------------------------------------------------------------


def _mock_response(status_code: int = 200, json_body: dict | None = None):
    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json = lambda: (json_body or {"data": [], "page": 1})
    mock_resp.text = ""
    return mock_resp


class TestDispatchUsesPostMethod:
    """Every other op is GET; this is the first POST. The provider must
    use the AsyncClient's ``post`` method, not ``get``."""

    async def test_uses_post_not_get(self) -> None:
        provider = FreshLinkedInProvider()

        with patch(
            "httpx.AsyncClient.post",
            new=AsyncMock(return_value=_mock_response()),
        ) as mock_post, patch(
            "httpx.AsyncClient.get",
            new=AsyncMock(return_value=_mock_response()),
        ) as mock_get:
            await provider.execute(
                operation="search_posts",
                params={"search_keywords": "GTM"},
                api_key=FAKE_KEY,
            )

        assert mock_post.await_count == 1
        assert mock_get.await_count == 0


class TestDispatchSendsFullPayloadAsJsonBody:
    async def test_json_body_has_all_twelve_keys(self) -> None:
        provider = FreshLinkedInProvider()

        with patch(
            "httpx.AsyncClient.post", new=AsyncMock(return_value=_mock_response()),
        ) as mock_post:
            await provider.execute(
                operation="search_posts",
                params={"search_keywords": "GTM", "page": 2},
                api_key=FAKE_KEY,
            )

        json_body = mock_post.await_args.kwargs.get("json")
        assert json_body is not None
        assert set(json_body.keys()) == set(_SEARCH_DEFAULT_PAYLOAD.keys())
        assert json_body["search_keywords"] == "GTM"
        assert json_body["page"] == 2
        # Unused filters are canonical empties.
        assert json_body["from_member"] == []
        assert json_body["date_posted"] == ""


# ---------------------------------------------------------------------------
# Fail-fast: empty-filter body never hits upstream
# ---------------------------------------------------------------------------


class TestEmptyFilterBodyDoesNotReachUpstream:
    async def test_no_http_call_when_filters_empty(self) -> None:
        provider = FreshLinkedInProvider()

        with patch(
            "httpx.AsyncClient.post", new=AsyncMock(return_value=_mock_response()),
        ) as mock_post:
            with pytest.raises(ProviderError) as exc_info:
                await provider.execute(
                    operation="search_posts",
                    params={"page": 1},
                    api_key=FAKE_KEY,
                )

        assert exc_info.value.status_code == 400
        assert mock_post.await_count == 0


# ---------------------------------------------------------------------------
# supported_operations contract
# ---------------------------------------------------------------------------


class TestSearchPostsListedAsSupportedOperation:
    def test_listed(self) -> None:
        assert "search_posts" in FreshLinkedInProvider.supported_operations
