"""Phase 2.2 + 2.4 — ``Post`` normalizer (single stable shape across all four
post-returning ops: fetch_profile_posts, fetch_company_posts, fetch_post_details,
search_posts).

Two APIs under test:
- ``_normalize_fresh_linkedin_post_item(raw_post, operation)`` — per-item helper.
  Used directly for the shape-invariance assertions.
- ``normalize_post(raw, provider, operation)`` — envelope-level dispatcher.
  Used for envelope-shape assertions.

The critical behavioural contract is **shape invariance**: callers who read
a ``Post`` dict never have to know which upstream op produced it. Phase 2.4's
``search_posts`` endpoint returns a slightly different per-post shape and
the normalizer coalesces it into the same stable `Post` dict (HLD 2.4 §4.2).

Pure function. Zero mocks.
"""

from __future__ import annotations

import pytest

from server.execution.normalizer import (
    _normalize_fresh_linkedin_post_item,
    _normalize_fresh_linkedin_post_list,
    normalize_post,
)

from tests.unit_tests.execution.fixtures import (
    company_posts_response,
    post_details_response,
    profile_posts_response,
    search_posts_response,
)


# Every stable key the normalizer may emit for a Post. Individual endpoints
# may legitimately omit fields the vendor doesn't return (e.g. company_posts
# doesn't give num_reactions / num_reposts) — top-level is bounded by this set.
STABLE_POST_KEYS = frozenset({
    "urn", "post_url", "posted_at", "text",
    "poster", "reshared",
    "num_likes", "num_comments", "num_reactions", "num_reposts",
    "images", "additional_data",
})


# ---------------------------------------------------------------------------
# fetch_profile_posts (O3)
# ---------------------------------------------------------------------------


def _first_profile_post_raw() -> dict:
    return profile_posts_response()["data"][0]


class TestProfilePostShape:
    def test_stable_keys_present(self) -> None:
        post = _normalize_fresh_linkedin_post_item(
            _first_profile_post_raw(), operation="fetch_profile_posts",
        )
        for key in ("urn", "post_url", "posted_at", "text", "poster",
                    "num_likes", "num_comments", "num_reactions",
                    "num_reposts", "images"):
            assert key in post, f"stable post shape missing '{key}'"

    def test_urn_is_bare_activity_id(self) -> None:
        post = _normalize_fresh_linkedin_post_item(
            _first_profile_post_raw(), operation="fetch_profile_posts",
        )
        assert post["urn"] == "7451139979067449344"

    def test_posted_at_comes_from_posted_field(self) -> None:
        post = _normalize_fresh_linkedin_post_item(
            _first_profile_post_raw(), operation="fetch_profile_posts",
        )
        assert post["posted_at"] == "2026-04-18 05:30:06"

    def test_poster_built_from_first_plus_last(self) -> None:
        post = _normalize_fresh_linkedin_post_item(
            _first_profile_post_raw(), operation="fetch_profile_posts",
        )
        assert post["poster"]["name"] == "Mohnish Kewlani"

    def test_poster_urn_preserved(self) -> None:
        post = _normalize_fresh_linkedin_post_item(
            _first_profile_post_raw(), operation="fetch_profile_posts",
        )
        assert post["poster"]["urn"] == "ACoAABd-rmEBHRUgerflqIUtd6_1UK3nFIoyDf4"

    def test_poster_headline_preserved(self) -> None:
        post = _normalize_fresh_linkedin_post_item(
            _first_profile_post_raw(), operation="fetch_profile_posts",
        )
        assert post["poster"]["headline"] == (
            "AI First Leader - Go-to-market Strategy & Operations | Saas"
        )

    def test_reshared_bool_passes_through(self) -> None:
        post = _normalize_fresh_linkedin_post_item(
            _first_profile_post_raw(), operation="fetch_profile_posts",
        )
        assert post["reshared"] is False


class TestProfilePostsEnvelopePagination:
    """Phase 2.5 (HLD §6.2) — list-envelope emits a nested ``pagination`` sub-dict
    carrying ``start`` + ``pagination_token`` (both ``str | None``). Old top-level
    ``cursor`` key is gone (HLD §14 breaking change)."""

    def test_pagination_sub_dict_present(self) -> None:
        envelope = normalize_post(
            profile_posts_response(),
            provider="fresh_linkedin",
            operation="fetch_profile_posts",
        )
        assert "pagination" in envelope
        assert isinstance(envelope["pagination"], dict)

    def test_pagination_keys_are_start_and_token(self) -> None:
        envelope = normalize_post(
            profile_posts_response(),
            provider="fresh_linkedin",
            operation="fetch_profile_posts",
        )
        assert set(envelope["pagination"].keys()) == {"start", "pagination_token"}

    def test_token_and_start_lifted_from_vendor_response(self) -> None:
        """HLD §10 — normalizer echoes ``pagination_token`` and ``start`` the
        vendor ships in the response body onto ``envelope.pagination.*``.
        Synthetic raw for determinism; fixture data is captured live and its
        precise shape (top-level vs nested ``paging``) drifts — impl owns
        where to look, the contract is just 'surface what the vendor sent'."""
        raw = {
            "data": [],
            "total": 50,
            "pagination_token": "tok1",
            "start": "10",
        }
        envelope = _normalize_fresh_linkedin_post_list(raw, "fetch_profile_posts")
        assert envelope["pagination"]["pagination_token"] == "tok1"
        assert envelope["pagination"]["start"] == "10"

    def test_legacy_top_level_cursor_removed(self) -> None:
        envelope = normalize_post(
            profile_posts_response(),
            provider="fresh_linkedin",
            operation="fetch_profile_posts",
        )
        assert "cursor" not in envelope


class TestProfilePostsEndOfStreamShape:
    """HLD §6.4 — when the vendor returns no pagination signals, envelope
    surfaces both values as ``None`` so the caller knows to stop."""

    def test_both_values_none_when_vendor_omits(self) -> None:
        raw = {"data": [], "total": 0}
        envelope = _normalize_fresh_linkedin_post_list(raw, "fetch_profile_posts")
        assert envelope["pagination"]["start"] is None
        assert envelope["pagination"]["pagination_token"] is None


class TestCompanyPostsEnvelopePagination:
    def test_pagination_sub_dict_present(self) -> None:
        envelope = normalize_post(
            company_posts_response(),
            provider="fresh_linkedin",
            operation="fetch_company_posts",
        )
        assert "pagination" in envelope
        assert set(envelope["pagination"].keys()) == {"start", "pagination_token"}

    def test_token_and_start_lifted_from_vendor_response(self) -> None:
        raw = {
            "data": [],
            "total": 50,
            "pagination_token": "company-tok",
            "start": "50",
        }
        envelope = _normalize_fresh_linkedin_post_list(raw, "fetch_company_posts")
        assert envelope["pagination"]["pagination_token"] == "company-tok"
        assert envelope["pagination"]["start"] == "50"


class TestProfilePostTopLevelBounded:
    def test_top_level_is_subset_of_stable_keys(self) -> None:
        post = _normalize_fresh_linkedin_post_item(
            _first_profile_post_raw(), operation="fetch_profile_posts",
        )
        stray = set(post.keys()) - STABLE_POST_KEYS
        assert stray == set(), f"non-stable keys leaked: {sorted(stray)}"


# ---------------------------------------------------------------------------
# fetch_company_posts (O4)
# ---------------------------------------------------------------------------


def _first_company_post_raw() -> dict:
    return company_posts_response()["data"][0]


class TestCompanyPostShape:
    def test_post_url_populated(self) -> None:
        """Company posts use ``url`` at the vendor level — the normalizer must
        still populate the stable ``post_url`` key."""
        post = _normalize_fresh_linkedin_post_item(
            _first_company_post_raw(), operation="fetch_company_posts",
        )
        assert post["post_url"].startswith("https://www.linkedin.com/")

    def test_poster_name_preserved(self) -> None:
        post = _normalize_fresh_linkedin_post_item(
            _first_company_post_raw(), operation="fetch_company_posts",
        )
        assert post["poster"]["name"] == "Google"

    def test_poster_has_no_urn_from_company_post(self) -> None:
        """Company posts don't carry ``poster.urn`` — absent / None either way."""
        post = _normalize_fresh_linkedin_post_item(
            _first_company_post_raw(), operation="fetch_company_posts",
        )
        assert post["poster"].get("urn") in (None, "")


class TestCompanyPostGranularReactionCounts:
    """Company posts carry rich reaction-type sub-counts. These go to
    additional_data — the normalized Post has only the aggregate ``num_reactions``
    (when vendor provides one) and ``num_likes``."""

    def test_reaction_subcounts_in_additional_data(self) -> None:
        post = _normalize_fresh_linkedin_post_item(
            _first_company_post_raw(), operation="fetch_company_posts",
        )
        extras = post.get("additional_data") or {}
        assert extras.get("num_empathy") == 12
        assert extras.get("num_interests") == 15
        assert extras.get("num_praises") == 24
        assert extras.get("num_appreciations") == 4


# ---------------------------------------------------------------------------
# fetch_post_details (O7)
# ---------------------------------------------------------------------------


class TestPostDetailsEnvelope:
    """Unlike list endpoints, post_details returns a single-object envelope
    under the ``post`` key (HLD 2.2 §3.3)."""

    def test_envelope_carries_post_singular(self) -> None:
        envelope = normalize_post(
            post_details_response(),
            provider="fresh_linkedin",
            operation="fetch_post_details",
        )
        assert "post" in envelope
        assert isinstance(envelope["post"], dict)
        assert envelope.get("enrichment_sources") == \
            {"fresh_linkedin": ["post_details"]}


class TestPostDetailsShape:
    def test_poster_type_passes_through(self) -> None:
        """HLD §5.1 — type comes from vendor when present. post_details has type='person'."""
        envelope = normalize_post(
            post_details_response(),
            provider="fresh_linkedin",
            operation="fetch_post_details",
        )
        post = envelope["post"]
        assert post["poster"].get("type") == "person"

    def test_reshared_bool_passed_through(self) -> None:
        envelope = normalize_post(
            post_details_response(),
            provider="fresh_linkedin",
            operation="fetch_post_details",
        )
        # Fixture has reshared=false.
        assert envelope["post"]["reshared"] is False


# ---------------------------------------------------------------------------
# search_posts (O8) — cross-endpoint coalescing
# ---------------------------------------------------------------------------


def _search_posts_raw() -> list:
    return search_posts_response()["data"]


def _first_search_post() -> dict:
    return _search_posts_raw()[0]


def _reshared_search_post() -> dict:
    """The Gates Foundation post reshared by Bill Gates (has ``original_post``)."""
    for post in _search_posts_raw():
        if "original_post" in post:
            return post
    raise AssertionError("Fixture expected to contain a reshared post with original_post")


class TestSearchPostsEnvelope:
    def test_envelope_has_posts_list(self) -> None:
        envelope = normalize_post(
            search_posts_response(),
            provider="fresh_linkedin",
            operation="search_posts",
        )
        assert isinstance(envelope.get("posts"), list)


class TestSearchPostsPaginationEnvelope:
    """Phase 2.5 — ``search_posts.page`` moves under the unified
    ``pagination`` sub-dict (HLD §6.2). Top-level ``page`` is removed
    (HLD §14 breaking change — staging-only, no BC concern)."""

    def test_pagination_sub_dict_present(self) -> None:
        envelope = normalize_post(
            search_posts_response(),
            provider="fresh_linkedin",
            operation="search_posts",
        )
        assert "pagination" in envelope
        assert isinstance(envelope["pagination"], dict)

    def test_pagination_carries_page_as_int(self) -> None:
        """Unlike the GET ops, search_posts.page stays ``int`` — matches
        its input contract (HLD §6.2)."""
        envelope = normalize_post(
            search_posts_response(),
            provider="fresh_linkedin",
            operation="search_posts",
        )
        assert isinstance(envelope["pagination"]["page"], int)

    def test_top_level_page_key_removed(self) -> None:
        """Readers must pull ``envelope.pagination.page``, not a top-level
        ``page`` key. HLD §14 breaking change."""
        envelope = normalize_post(
            search_posts_response(),
            provider="fresh_linkedin",
            operation="search_posts",
        )
        assert "page" not in envelope


def _normalized_search_posts() -> list[dict]:
    """Full search envelope dispatched through ``normalize_post`` — this is
    where the cross-endpoint coalescing actually happens (the per-item helper
    doesn't know how to fold flat poster fields)."""
    envelope = normalize_post(
        search_posts_response(),
        provider="fresh_linkedin",
        operation="search_posts",
    )
    return envelope["posts"]


def _normalized_reshared_search_post() -> dict:
    for post in _normalized_search_posts():
        if post.get("reshared"):
            return post
    raise AssertionError("Expected at least one normalized reshared post from search_posts")


class TestSearchFlatPosterCoalesced:
    """Search endpoint emits flat ``poster_name``, ``poster_linkedin_url``,
    ``poster_title``. The envelope normalizer folds them into ``poster{}``."""

    def test_flat_poster_fields_folded(self) -> None:
        post = _normalized_search_posts()[0]
        assert post["poster"]["name"] == "Bill Gates"
        assert post["poster"]["linkedin_url"] == "https://www.linkedin.com/in/williamhgates"

    def test_poster_title_renamed_to_headline(self) -> None:
        post = _normalized_search_posts()[0]
        assert post["poster"]["headline"] == (
            "Chair, Gates Foundation and Founder, Breakthrough Energy"
        )


class TestSearchNumSharesMapsToNumReposts:
    def test_num_shares_renamed(self) -> None:
        post = _normalized_search_posts()[0]
        assert post["num_reposts"] == 51
        assert "num_shares" not in post


class TestSearchOriginalPostBecomesReshared:
    """Key cross-endpoint coalescing: search encodes reshares via nested
    ``original_post``. Normalizer must surface ``reshared: True`` plus a
    ``reshared_from`` sub-dict in ``additional_data`` (HLD 2.4 §4.2)."""

    def test_reshared_flag_true_when_original_post_present(self) -> None:
        post = _normalized_reshared_search_post()
        assert post["reshared"] is True

    def test_reshared_from_captures_original_urn_and_url(self) -> None:
        post = _normalized_reshared_search_post()
        extras = post.get("additional_data") or {}
        reshared_from = extras.get("reshared_from") or {}
        assert reshared_from.get("urn") is not None
        assert reshared_from.get("post_url", "").startswith("https://www.linkedin.com/")


# ---------------------------------------------------------------------------
# Shape invariance across all four endpoints — the anti-coupling guard
# ---------------------------------------------------------------------------


def _one_normalized_post_for_every_endpoint() -> list[tuple[dict, str]]:
    """Collect a single normalized ``Post`` via each endpoint's true path:
    list endpoints go through the envelope dispatcher, per-item for the rest.
    This mirrors how real callers get their Post dicts — important for the
    search case where coalescing only happens at the envelope level."""
    return [
        (
            _normalize_fresh_linkedin_post_item(
                _first_profile_post_raw(), operation="fetch_profile_posts",
            ),
            "fetch_profile_posts",
        ),
        (
            _normalize_fresh_linkedin_post_item(
                _first_company_post_raw(), operation="fetch_company_posts",
            ),
            "fetch_company_posts",
        ),
        (
            normalize_post(
                post_details_response(),
                provider="fresh_linkedin",
                operation="fetch_post_details",
            )["post"],
            "fetch_post_details",
        ),
        (_normalized_search_posts()[0], "search_posts"),
    ]


class TestShapeInvarianceAcrossEndpoints:
    """The whole point of the coalescing normalizer: a caller reading a Post
    shouldn't need to branch on which op produced it. Keys may be absent when
    the vendor doesn't provide them, but none should *leak* outside the stable
    set."""

    @pytest.mark.parametrize("post,label", _one_normalized_post_for_every_endpoint())
    def test_top_level_keys_are_bounded(self, post: dict, label: str) -> None:
        stray = set(post.keys()) - STABLE_POST_KEYS
        assert stray == set(), (
            f"{label} leaked non-stable keys: {sorted(stray)}"
        )

    @pytest.mark.parametrize("post,label", _one_normalized_post_for_every_endpoint())
    def test_every_endpoint_produces_a_poster_with_a_name(self, post: dict, label: str) -> None:
        assert isinstance(post["poster"], dict)
        assert post["poster"].get("name") is not None, (
            f"{label}: poster.name missing"
        )
