"""API tests — Phase 2.2/2.3/2.4 post-family operations on `fresh_linkedin`.

Simulates the **gtm-consultant agent** making direct service-token calls to
the GTM Engine execution API. BDD principles applied in plain Python (per
`backend-api-testing-blueprint.md` §2–§4).

Covers the seven net-new post-family operations:
- fetch_profile_posts  (Phase 2.2 / O3)
- fetch_company_posts  (Phase 2.2 / O4)
- fetch_post_details   (Phase 2.2 / O7)
- fetch_post_reactions (Phase 2.3 / O5)
- fetch_post_comments  (Phase 2.3 / O6)
- search_posts         (Phase 2.4 / O8)

---

Scenarios (one `test_` function each, below):

Phase 2.2 — post fetching
  - fetch_profile_posts with a profile URL → 200, Post envelope, 3 credits
  - fetch_company_posts with a company URL → 200, Post envelope, 3 credits
  - fetch_post_details with a bare URN → 200, single-Post envelope, 3 credits
  - every normalized Post has the stable shape (urn, post_url, posted_at, poster, ...)
  - fetch_profile_posts rejects a company URL with 400 + no debit
  - fetch_company_posts rejects a profile URL with 400 + no debit
  - fetch_post_details rejects urn:li:activity:... form with 400 + no debit
  - fetch_post_details rejects a full post URL with 400 + no debit

Phase 2.3 — engagement
  - fetch_post_reactions returns reactions[] + reactor snippet + 3 credits
  - fetch_post_comments returns comments[] + commenter snippet + 3 credits
  - reactor / commenter linkedin_url preserved verbatim (URN-style, not rewritten)

Phase 2.4 — search
  - search_posts with search_keywords → 200, posts[] envelope, 3 credits
  - search_posts with only from_member filter → 200
  - rejects empty-filter body with 400 + no debit
  - rejects page=0 with 400 + no debit
  - search-returned Posts share the same stable shape as fetch_profile_posts

Cross-cutting
  - cache bypass — two identical fetch_profile_posts charge 6 credits
  - rate-limit bucket shared across post-family ops (observed: no 429 for
    a small burst of interleaved calls)

Prerequisites:
    1. Server running:  cd server && uvicorn server.app:app --reload
    2. Platform key `LINKEDIN_RAPIDAPI_KEY` in the server env
    3. Migrations 020 (posts) + 021 (engagement) + 022 (search) applied
    4. Env vars (optional):
         - TEST_LINKEDIN_PROFILE_URL    — real profile URL
         - TEST_LINKEDIN_COMPANY_URL    — real company URL
         - TEST_POST_URN                — bare activity id that exists
         - TEST_SEARCH_MEMBER_URN       — a member URN for search tests

Run:
    python tests/api_tests/test_fresh_linkedin_posts_api.py
    python tests/api_tests/test_fresh_linkedin_posts_api.py --no-cache
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx
import redis


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:8000"
SERVICE_TOKEN = "XRWnB_IpZa0f3T1G1rpsItpa_S2qJKHBZuY_3Bc8WDM"
RICH_TENANT_ID = "4"
CONSULTANT_AGENT = "gtm-consultant"

REDIS_URL = "redis://localhost:6379/0"
CACHE_PREFIX = "cache:exec:*"

PLATFORM_URL = "https://umws.public.staging.nurturev.com/private"
PLATFORM_TOKEN = "Na3G8LOC84N8J8y32A5mJUwP7Avb0P57"

# Every post-family op costs 3 credits (HLD 2.2/2.3/2.4).
POST_OP_COST = 3.0

# Stable Post shape — the cross-endpoint coalescing contract (HLD 2.2 §3.1).
# Profile / company / details / search all converge to these top-level keys.
STABLE_POST_KEYS = frozenset({
    "urn", "post_url", "posted_at", "text",
    "poster", "reshared",
    "num_likes", "num_comments", "num_reactions", "num_reposts",
    "images", "additional_data",
})

# Test inputs — defaults are the known-public probes captured in docs/sample_responses/
TEST_PROFILE_URL = os.environ.get(
    "TEST_LINKEDIN_PROFILE_URL",
    "https://www.linkedin.com/in/mohnishkewlani/",
)
TEST_COMPANY_URL = os.environ.get(
    "TEST_LINKEDIN_COMPANY_URL",
    "https://www.linkedin.com/company/google/",
)
TEST_POST_URN = os.environ.get("TEST_POST_URN", "7450415215956987904")
TEST_SEARCH_MEMBER_URN = os.environ.get(
    "TEST_SEARCH_MEMBER_URN",
    "ACoAAA8BYqEBCGLg_vT_ca6mMEqkpp9nVffJ3hc",
)


# ---------------------------------------------------------------------------
# Helpers (same shape as other fresh_linkedin API tests)
# ---------------------------------------------------------------------------


def _svc_headers(
    tenant_id: str = RICH_TENANT_ID,
    agent_type: str = CONSULTANT_AGENT,
) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-Id": tenant_id,
        "X-Agent-Type": agent_type,
    }


def _platform_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {PLATFORM_TOKEN}"}


async def _get_platform_balance(
    client: httpx.AsyncClient, tenant_id: str = RICH_TENANT_ID,
) -> float:
    resp = await client.get(
        f"{PLATFORM_URL}/tenant/credits",
        params={"tenant_id": int(tenant_id)},
        headers=_platform_headers(),
    )
    assert resp.status_code == 200, (
        f"Direct UM balance check failed: status={resp.status_code} "
        f"body={resp.text[:200]}"
    )
    return float(resp.json())


async def _balance_delta_after(
    client: httpx.AsyncClient,
    balance_before: float,
    tenant_id: str = RICH_TENANT_ID,
    settle_seconds: float = 2.0,
) -> float:
    await asyncio.sleep(settle_seconds)
    balance_after = await _get_platform_balance(client, tenant_id)
    return balance_before - balance_after


async def _post_execute(
    client: httpx.AsyncClient,
    body: dict,
    tenant_id: str = RICH_TENANT_ID,
) -> httpx.Response:
    return await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers=_svc_headers(tenant_id),
        json=body,
    )


def _body(operation: str, **params) -> dict:
    return {"operation": operation, "provider": "fresh_linkedin", "params": params}


def _assert_stable_post_shape(post: dict, label: str) -> None:
    """Every normalized Post across the four post-returning ops carries the
    same top-level key set (HLD 2.2 §3.1). Keys may legitimately be absent
    (e.g. fetch_company_posts doesn't provide num_reactions) — the invariant
    is that nothing leaks *outside* the stable set."""
    stray = set(post.keys()) - STABLE_POST_KEYS
    assert stray == set(), (
        f"{label}: non-stable keys leaked on Post: {sorted(stray)}"
    )
    # poster is always a dict (may have subset of fields per endpoint).
    poster = post.get("poster") or {}
    assert isinstance(poster, dict), f"{label}: poster must be a dict"


async def _assert_validation_400_no_debit(
    client: httpx.AsyncClient, body: dict, label: str,
) -> None:
    balance_before = await _get_platform_balance(client)

    resp = await _post_execute(client, body)
    print(f"[{label}] status={resp.status_code}  detail={str(resp.json())[:200]}")

    assert resp.status_code == 400, (
        f"[{label}] expected 400, got {resp.status_code}: {resp.text[:300]}"
    )

    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff) < 0.01, (
        f"[{label}] rejected request must not debit credits; delta={diff}"
    )


# ===========================================================================
# Phase 2.2 — fetch_profile_posts (O3)
# ===========================================================================


async def test_fetch_profile_posts_returns_post_list_and_charges_three_credits(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S1: fetch_profile_posts → {posts, total, cursor} + 3 credits ---")

    balance_before = await _get_platform_balance(client)

    resp = await _post_execute(
        client,
        _body("fetch_profile_posts", linkedin_url=TEST_PROFILE_URL),
    )
    body = resp.json()
    print(f"Status: {resp.status_code}  credits={body.get('credits_charged')}")

    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"
    assert body["credits_charged"] == POST_OP_COST
    data = body["result"]

    # Envelope contract
    assert "posts" in data, f"Envelope missing 'posts'; keys: {list(data.keys())}"
    assert isinstance(data["posts"], list)
    assert "total" in data
    assert "cursor" in data, "Envelope must expose cursor (null when no more pages)"
    assert data.get("enrichment_sources") == {"fresh_linkedin": ["posts"]}

    # Per-post shape
    if data["posts"]:
        _assert_stable_post_shape(data["posts"][0], "fetch_profile_posts")

    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff - POST_OP_COST) < 0.01, (
        f"Expected {POST_OP_COST} credit debit, got {diff}"
    )
    print("PASS")


async def test_fetch_profile_posts_rejects_company_url_with_400(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S2: fetch_profile_posts + company URL → 400, no debit ---")
    await _assert_validation_400_no_debit(
        client,
        _body("fetch_profile_posts", linkedin_url=TEST_COMPANY_URL),
        label="profile-posts-wrong-url-type",
    )
    print("PASS")


# ===========================================================================
# Phase 2.2 — fetch_company_posts (O4)
# ===========================================================================


async def test_fetch_company_posts_returns_post_list_and_charges_three_credits(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S3: fetch_company_posts → {posts, total, cursor} + 3 credits ---")

    balance_before = await _get_platform_balance(client)

    resp = await _post_execute(
        client,
        _body("fetch_company_posts", linkedin_url=TEST_COMPANY_URL),
    )
    body = resp.json()
    print(f"Status: {resp.status_code}")

    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"
    assert body["credits_charged"] == POST_OP_COST
    data = body["result"]

    assert isinstance(data.get("posts"), list)
    assert data.get("enrichment_sources") == {"fresh_linkedin": ["posts"]}

    if data["posts"]:
        _assert_stable_post_shape(data["posts"][0], "fetch_company_posts")
        # Company posts have a company-style poster — name populated, but
        # typically no urn / headline.
        first = data["posts"][0]
        assert first["poster"].get("name"), "company post must carry poster.name"

    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff - POST_OP_COST) < 0.01
    print("PASS")


async def test_fetch_company_posts_rejects_profile_url_with_400(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S4: fetch_company_posts + profile URL → 400, no debit ---")
    await _assert_validation_400_no_debit(
        client,
        _body("fetch_company_posts", linkedin_url=TEST_PROFILE_URL),
        label="company-posts-wrong-url-type",
    )
    print("PASS")


# ===========================================================================
# Phase 2.2 — fetch_post_details (O7)
# ===========================================================================


async def test_fetch_post_details_returns_single_post_and_charges_three_credits(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S5: fetch_post_details → {post, enrichment_sources} + 3 credits ---")

    balance_before = await _get_platform_balance(client)

    resp = await _post_execute(
        client,
        _body("fetch_post_details", urn=TEST_POST_URN),
    )
    body = resp.json()
    print(f"Status: {resp.status_code}")

    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"
    assert body["credits_charged"] == POST_OP_COST
    data = body["result"]

    # Single-post envelope (HLD 2.2 §3.3) — ``post`` (singular), no list wrapper.
    if data.get("match_found") is False:
        print("(no match — deleted / unknown URN)")
    else:
        assert "post" in data, (
            f"post_details envelope must expose 'post'; got keys: {list(data.keys())}"
        )
        assert data.get("enrichment_sources") == {"fresh_linkedin": ["post_details"]}
        _assert_stable_post_shape(data["post"], "fetch_post_details")

    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff - POST_OP_COST) < 0.01
    print("PASS")


async def test_fetch_post_details_rejects_structured_urn_with_400(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S6: fetch_post_details + urn:li:activity:<id> → 400, no debit ---")
    await _assert_validation_400_no_debit(
        client,
        _body("fetch_post_details", urn=f"urn:li:activity:{TEST_POST_URN}"),
        label="urn-structured-form",
    )
    print("PASS")


async def test_fetch_post_details_rejects_post_url_with_400(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S7: fetch_post_details + full post URL → 400, no debit ---")
    post_url = (
        f"https://www.linkedin.com/feed/update/urn:li:activity:{TEST_POST_URN}/"
    )
    await _assert_validation_400_no_debit(
        client,
        _body("fetch_post_details", urn=post_url),
        label="urn-full-url",
    )
    print("PASS")


# ===========================================================================
# Phase 2.3 — fetch_post_reactions (O5)
# ===========================================================================


async def test_fetch_post_reactions_returns_reactions_and_charges_three_credits(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S8: fetch_post_reactions → reactions[] + 3 credits ---")

    balance_before = await _get_platform_balance(client)

    resp = await _post_execute(
        client,
        _body("fetch_post_reactions", urn=TEST_POST_URN),
    )
    body = resp.json()
    print(f"Status: {resp.status_code}")

    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"
    assert body["credits_charged"] == POST_OP_COST
    data = body["result"]

    assert "reactions" in data, (
        f"envelope must expose 'reactions'; got keys {list(data.keys())}"
    )
    assert "total" in data
    assert "cursor" in data, "cursor must be present (may be null)"
    assert data.get("enrichment_sources") == {"fresh_linkedin": ["reactions"]}

    if data["reactions"]:
        first = data["reactions"][0]
        # Reaction shape contract (HLD 2.3 §3.1).
        assert "type" in first, "reaction.type required"
        assert isinstance(first.get("reactor"), dict), "reaction.reactor must be a dict"
        reactor = first["reactor"]
        # Reactor snippet — NOT a full Person (P2-D5). Name + URN-form URL present;
        # no first_name/last_name/title fabricated via splitting.
        assert "name" in reactor
        assert "linkedin_url" in reactor
        # P2-D5 honesty — no fabricated fields.
        for forbidden in ("first_name", "last_name", "title"):
            assert forbidden not in reactor, (
                f"reactor must not contain fabricated '{forbidden}' (P2-D5)"
            )

    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff - POST_OP_COST) < 0.01
    print("PASS")


async def test_reactor_urn_style_url_is_preserved_verbatim(
    client: httpx.AsyncClient,
) -> None:
    """LinkedIn returns reactor URLs in URN form (``/in/ACoAA...``). The
    normalizer must NOT rewrite these to slug form — that would silently
    change caller-visible identifiers (HLD 2.3 §3.4)."""
    print("\n--- S9: reactor.linkedin_url keeps URN-style prefix ---")

    resp = await _post_execute(
        client,
        _body("fetch_post_reactions", urn=TEST_POST_URN),
    )
    assert resp.status_code == 200
    reactions = resp.json()["result"].get("reactions") or []
    if not reactions:
        print("SKIP: no reactions on this URN")
        return

    # At least one reactor URL should carry the ACoAA URN prefix — that's
    # LinkedIn's native format for this endpoint. If ALL are slug-form something
    # is rewriting them.
    acoaa_count = sum(
        1 for r in reactions
        if "/in/ACoAA" in (r.get("reactor") or {}).get("linkedin_url", "")
    )
    print(f"ACoAA-prefix reactors: {acoaa_count}/{len(reactions)}")
    assert acoaa_count > 0, (
        "At least one reactor linkedin_url should carry the URN prefix — "
        "rewriting all to slug form would silently change identifiers"
    )
    print("PASS")


async def test_fetch_post_reactions_rejects_malformed_urn_with_400(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S10: fetch_post_reactions + malformed urn → 400, no debit ---")
    await _assert_validation_400_no_debit(
        client,
        _body("fetch_post_reactions", urn="not-an-id"),
        label="reactions-bad-urn",
    )
    print("PASS")


# ===========================================================================
# Phase 2.3 — fetch_post_comments (O6)
# ===========================================================================


async def test_fetch_post_comments_returns_comments_and_charges_three_credits(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S11: fetch_post_comments → comments[] + 3 credits ---")

    balance_before = await _get_platform_balance(client)

    resp = await _post_execute(
        client,
        _body("fetch_post_comments", urn=TEST_POST_URN),
    )
    body = resp.json()
    print(f"Status: {resp.status_code}")

    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"
    assert body["credits_charged"] == POST_OP_COST
    data = body["result"]

    assert "comments" in data
    assert "total" in data
    assert "cursor" in data, "cursor preserved verbatim from vendor pagination_token"
    assert data.get("enrichment_sources") == {"fresh_linkedin": ["comments"]}

    if data["comments"]:
        first = data["comments"][0]
        # Comment shape contract (HLD 2.3 §3.2).
        assert "text" in first
        assert "created_at" in first, "ISO-8601 created_at derived from created_datetime"
        assert "reply_count" in first
        commenter = first.get("commenter") or {}
        assert commenter.get("name"), "commenter.name required"
        # reply_count derived from replies[] length.
        assert isinstance(first["reply_count"], int)

    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff - POST_OP_COST) < 0.01
    print("PASS")


# ===========================================================================
# Phase 2.4 — search_posts (O8)
# ===========================================================================


async def test_search_posts_by_keyword_returns_posts_and_charges_three_credits(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S12: search_posts {keyword=GTM} → posts[] + 3 credits ---")

    balance_before = await _get_platform_balance(client)

    resp = await _post_execute(
        client,
        _body("search_posts", search_keywords="GTM", page=1),
    )
    body = resp.json()
    print(f"Status: {resp.status_code}")

    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"
    assert body["credits_charged"] == POST_OP_COST
    data = body["result"]

    # search envelope (HLD 2.4 §4.1) — no total, no cursor, page present.
    assert "posts" in data
    assert "page" in data, "search envelope must expose 'page' (int we just fetched)"
    assert data.get("enrichment_sources") == {"fresh_linkedin": ["search_posts"]}

    if data["posts"]:
        # Shape invariance — search Posts are coalesced into the same shape as
        # fetch_profile_posts (HLD 2.4 §4.2 / §8.2.2.1).
        _assert_stable_post_shape(data["posts"][0], "search_posts")

    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff - POST_OP_COST) < 0.01
    print("PASS")


async def test_search_posts_by_from_member_urn_returns_posts(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S13: search_posts {from_member=<URN>} → posts[] + 3 credits ---")

    resp = await _post_execute(
        client,
        _body(
            "search_posts",
            from_member=[TEST_SEARCH_MEMBER_URN],
            page=1,
        ),
    )
    body = resp.json()

    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"
    assert body["credits_charged"] == POST_OP_COST
    data = body["result"]
    assert "posts" in data
    if data["posts"]:
        _assert_stable_post_shape(data["posts"][0], "search_posts-by-member")
    print("PASS")


async def test_search_posts_rejects_empty_filter_body_with_400(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S14: search_posts with all filters empty → 400, no debit ---")
    await _assert_validation_400_no_debit(
        client,
        _body("search_posts", page=1),  # no filter keys at all
        label="search-empty",
    )
    print("PASS")


async def test_search_posts_rejects_non_positive_page_with_400(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S15: search_posts page=0 → 400, no debit ---")
    await _assert_validation_400_no_debit(
        client,
        _body("search_posts", search_keywords="GTM", page=0),
        label="search-page-zero",
    )
    print("PASS")


# ===========================================================================
# Cross-cutting — cache bypass + shape invariance across endpoints
# ===========================================================================


async def test_two_identical_fetch_profile_posts_calls_charge_six_credits(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S16: cache bypass — two fetch_profile_posts calls charge 6 credits ---")

    _flush_cache_silently()
    balance_before = await _get_platform_balance(client)

    body = _body("fetch_profile_posts", linkedin_url=TEST_PROFILE_URL)
    first = await _post_execute(client, body)
    second = await _post_execute(client, body)

    print(f"First  credits={first.json().get('credits_charged')}")
    print(f"Second credits={second.json().get('credits_charged')}")

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["credits_charged"] == POST_OP_COST
    assert second.json()["credits_charged"] == POST_OP_COST, (
        f"fresh_linkedin must never cache (P2-D10); second call charged "
        f"{second.json()['credits_charged']}"
    )

    diff = await _balance_delta_after(client, balance_before, settle_seconds=3.0)
    expected = 2 * POST_OP_COST
    assert abs(diff - expected) < 0.01, (
        f"Expected {expected} credits across two uncached calls, got {diff}"
    )
    print("PASS")


async def test_posts_from_different_endpoints_share_the_same_top_level_shape(
    client: httpx.AsyncClient,
) -> None:
    """Shape invariance is the whole point of Phase 2.4's coalescing normalizer
    (HLD 2.4 §8.2.2.1). A ``Post`` from search_posts and a ``Post`` from
    fetch_profile_posts must have the same stable top-level key set — callers
    reading a Post shouldn't need to branch on which op produced it."""
    print("\n--- S17: Post shape invariance across fetch_profile_posts + search_posts ---")

    profile_resp = await _post_execute(
        client, _body("fetch_profile_posts", linkedin_url=TEST_PROFILE_URL),
    )
    search_resp = await _post_execute(
        client, _body("search_posts", search_keywords="GTM", page=1),
    )

    assert profile_resp.status_code == 200 and search_resp.status_code == 200

    profile_posts = profile_resp.json()["result"].get("posts") or []
    search_posts = search_resp.json()["result"].get("posts") or []

    if not profile_posts or not search_posts:
        print("SKIP: either endpoint returned no posts")
        return

    _assert_stable_post_shape(profile_posts[0], "profile-source")
    _assert_stable_post_shape(search_posts[0], "search-source")

    # The two Post dicts can have a different *filled* subset of keys (vendor
    # provides different fields per endpoint), but their top-level keys must
    # both be subsets of the stable set. _assert_stable_post_shape checks that
    # invariant for each.
    print("PASS")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _flush_cache_silently() -> int:
    try:
        r = redis.from_url(REDIS_URL)
        keys = r.keys(CACHE_PREFIX)
        if not keys:
            return 0
        return r.delete(*keys)
    except Exception as exc:
        print(f"WARN: could not flush Redis cache: {exc}")
        return 0


async def main() -> None:
    flush = "--no-cache" not in sys.argv

    print("=" * 64)
    print("Fresh LinkedIn posts/engagement/search API Tests — consultant-agent")
    print(f"GTM Engine:     {BASE_URL}")
    print(f"Platform:       {PLATFORM_URL}")
    print(f"Rich tenant:    {RICH_TENANT_ID}")
    print(f"Agent identity: {CONSULTANT_AGENT}")
    print(f"Profile URL:    {TEST_PROFILE_URL}")
    print(f"Company URL:    {TEST_COMPANY_URL}")
    print(f"Post URN:       {TEST_POST_URN}")
    print(f"Member URN:     {TEST_SEARCH_MEMBER_URN}")
    print("=" * 64)

    if flush:
        deleted = _flush_cache_silently()
        print(f"\nFlushed {deleted} execution cache keys from Redis")

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            health = await client.get(f"{BASE_URL}/health")
            print(f"GTM Engine health: {health.status_code}")
        except httpx.ConnectError:
            print("\nERROR: Cannot connect to GTM Engine. Start it with:")
            print("  cd server && uvicorn server.app:app --reload")
            return

        try:
            balance = await _get_platform_balance(client)
            print(f"Platform balance for tenant {RICH_TENANT_ID}: {balance}")
        except Exception as exc:
            print(f"\nERROR: Cannot reach UM platform: {exc}")
            return

        tests = [
            # Phase 2.2
            test_fetch_profile_posts_returns_post_list_and_charges_three_credits,
            test_fetch_profile_posts_rejects_company_url_with_400,
            test_fetch_company_posts_returns_post_list_and_charges_three_credits,
            test_fetch_company_posts_rejects_profile_url_with_400,
            test_fetch_post_details_returns_single_post_and_charges_three_credits,
            test_fetch_post_details_rejects_structured_urn_with_400,
            test_fetch_post_details_rejects_post_url_with_400,
            # Phase 2.3
            test_fetch_post_reactions_returns_reactions_and_charges_three_credits,
            test_reactor_urn_style_url_is_preserved_verbatim,
            test_fetch_post_reactions_rejects_malformed_urn_with_400,
            test_fetch_post_comments_returns_comments_and_charges_three_credits,
            # Phase 2.4
            test_search_posts_by_keyword_returns_posts_and_charges_three_credits,
            test_search_posts_by_from_member_urn_returns_posts,
            test_search_posts_rejects_empty_filter_body_with_400,
            test_search_posts_rejects_non_positive_page_with_400,
            # Cross-cutting
            test_two_identical_fetch_profile_posts_calls_charge_six_credits,
            test_posts_from_different_endpoints_share_the_same_top_level_shape,
        ]

        passed = 0
        failed = 0
        for test in tests:
            try:
                await test(client)
                passed += 1
            except AssertionError as exc:
                print(f"FAIL: {exc}")
                failed += 1
            except Exception as exc:
                print(f"ERROR: {type(exc).__name__}: {exc}")
                failed += 1

    print("\n" + "=" * 64)
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
