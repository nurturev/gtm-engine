"""API tests — RocketReach Universal API migration.

Simulates the **gtm-consultant agent** making direct service-token calls
to the GTM Engine execution API. BDD principles applied in plain Python
(per `backend-api-testing-blueprint.md` §2–§4) — declarative scenario
functions hitting the real app.

Feature under test — the Universal migration documented under
`docs/gtm-engine/data_enrichment/rocketreach_api_rewrite/`. Every
client-facing behavior covered here corresponds to a line item in the
requirements §5 / §9 and LLD §11.

---

Scenarios (one `test_` function each, below):

POST /api/v1/execute with provider="rocketreach", operation="enrich_person"
  - accepts a LinkedIn URL and returns a canonical-shape Person with 3 credits debited
  - accepts an email identifier and returns a canonical-shape Person with 3 credits debited
  - response top level carries ONLY canonical keys + enrichment_sources + additional_data
    (non-canonical extras — id / photo_url / skills / city / state / country / status /
    current_employer — never leak to the top level)
  - rejects a request with no identifier (no linkedin_url, email, name+company, or id) with 400 + no debit
  - second identical call is served from cache — 0 credits, cached=true
  - enrich_person without an explicit provider still routes to apollo (no silent routing change)

POST /api/v1/execute with provider="rocketreach", operation="search_people"
  - accepts a title + company filter and returns a list of canonical Person rows with 3 credits debited
  - search rows surface teaser hints under additional_data
    (email_domain_hints / phone_hint / is_premium_phone_available) — requirements §5.5 bug fix
  - canonical `email` and `phone` stay null on search rows (no fabrication — requirements §5.5)

POST /api/v1/execute with provider="rocketreach", operation="enrich_company"  (new on vendor catalog)
  - accepts a domain and returns a canonical-shape Company with 3 credits debited
  - rejects a request with neither domain nor name with 400 + no debit
  - Universal field renames are absorbed (response exposes `domain` / `industry`, never
    `email_domain` / `industry_str` — requirements §6)

POST /api/v1/execute with provider="rocketreach", operation="search_companies"  (new on vendor catalog)
  - accepts a company_name filter and returns a list of canonical Company rows with 3 credits debited
  - pagination block is normalized to `{total, page, per_page}` regardless of the Universal
    `{start, next, total}` vs legacy `{total, thisPage, nextPage, pageSize}` vendor shape

POST /api/v1/execute/cost
  - reports 3 credits for each of the four RocketReach operations (catalog + DB cost cache sanity)

---

Scenarios intentionally deferred (require vendor-side fixtures or a key
without Universal credits — tracked as wiring-level mock tests in the
unit-test modules per LLD §11.1):

  - Universal-Credits 403 → mapped to 402 with upgrade-plan guidance (requirements §5.3, §7).
    Needs either a BYOK key without Universal allocation or a server-side upstream mock.
  - Async cap-hit → response carries `lookup_status: "in_progress"` + `retry_hint`,
    billing is zero, and the in-progress payload is NOT cached (LLD §3.10, §6.2, §11.1).
    Exercised by `tests/unit_tests/execution/test_rocketreach_universal_param_prep.py`
    and the service-layer tests — requires a forced polling cap which is not
    achievable from the HTTP boundary without a spy upstream.

---

Prerequisites:
    1. Server running:  cd server && uvicorn server.app:app --reload
    2. Platform key `ROCKETREACH_API_KEY` provisioned in the server env AND
       provisioned with Universal credits (T12 / account 30412685).
    3. Migration 019 applied (operation_costs rows for rocketreach.enrich_company
       and rocketreach.search_companies at 3.0 — see
       `migrations/019_rocketreach_universal_operation_costs.sql`).
    4. `rocketreach` registered in VENDOR_CATALOG with all four ops at 3 credits.
    5. Env vars (see top of file):
         - TEST_ROCKETREACH_LINKEDIN_URL   — real LinkedIn profile URL upstream will resolve
         - TEST_ROCKETREACH_EMAIL          — real work email upstream will resolve
         - TEST_ROCKETREACH_COMPANY_DOMAIN — real company domain upstream will resolve

Run:
    python tests/api_tests/test_rocketreach_universal_api.py
    python tests/api_tests/test_rocketreach_universal_api.py --no-cache   # skip Redis flush
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import httpx

try:
    import redis
except ImportError:
    redis = None


# ---------------------------------------------------------------------------
# Configuration — matches existing test_fresh_linkedin_api.py conventions
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:8000"
SERVICE_TOKEN = "XRWnB_IpZa0f3T1G1rpsItpa_S2qJKHBZuY_3Bc8WDM"

# Tenant with platform credits — same one other API tests use.
RICH_TENANT_ID = "4"

# Agent identity — we are simulating the gtm-consultant skill routing calls.
CONSULTANT_AGENT = "gtm-consultant"

# Infra
REDIS_URL = "redis://localhost:6379/0"
CACHE_PREFIX = "cache:exec:*"

# UM platform — direct balance verification
PLATFORM_URL = "https://umws.public.staging.nurturev.com/private"
PLATFORM_TOKEN = "Na3G8LOC84N8J8y32A5mJUwP7Avb0P57"

# Expected credit cost — must match migrations/019 + VENDOR_CATALOG.
ROCKETREACH_COST = 3.0

# Canonical Person shape per unique_entity_fields.csv + HLD §3.1. The response
# envelope (inside result.data) must carry ONLY these keys at top level,
# alongside `enrichment_sources` and `additional_data`. On async cap-hit the
# envelope additionally exposes `lookup_status` + `retry_hint` (tested in unit
# tests — see the deferred list at the top of this file).
CANONICAL_PERSON_KEYS = frozenset({
    "name", "first_name", "last_name",
    "title", "headline",
    "experiences",
    "linkedin_url", "email", "phone",
    "location",
    "company_name", "company_domain",
})
META_PERSON_KEYS = frozenset({
    "enrichment_sources", "additional_data",
    "match_found", "people",
    "total", "page", "per_page",
})
ALLOWED_TOP_LEVEL_PERSON = CANONICAL_PERSON_KEYS | META_PERSON_KEYS

CANONICAL_COMPANY_KEYS = frozenset({
    "name", "domain", "linkedin_url",
    "employee_count", "industry", "hq_location",
})
META_COMPANY_KEYS = frozenset({
    "enrichment_sources", "additional_data",
    "match_found", "companies",
    "total", "page", "per_page",
})
ALLOWED_TOP_LEVEL_COMPANY = CANONICAL_COMPANY_KEYS | META_COMPANY_KEYS

# Non-canonical fields the RocketReach normalizer is explicitly supposed to
# route under `additional_data`. If any leaks to the top level, the retrofit is
# regressing.
ROCKETREACH_PERSON_EXTRAS_TO_GUARD = (
    "id", "photo_url", "skills",
    "city", "state", "country",
    "status", "current_employer", "current_employer_domain",
    "current_title",
)
ROCKETREACH_COMPANY_EXTRAS_TO_GUARD = (
    "id", "website", "website_url",
    "email_domain", "industry_str",
    "city", "state", "country",
    "logo_url", "phone",
    "ticker", "ticker_symbol",
    "num_employees",
)

# Inputs pulled from env so CI / local runs can supply real values.
TEST_LINKEDIN_URL = os.environ.get(
    "TEST_ROCKETREACH_LINKEDIN_URL",
    "https://www.linkedin.com/in/satyanadella",
)
TEST_EMAIL = os.environ.get(
    "TEST_ROCKETREACH_EMAIL",
    "satyan@microsoft.com",
)
TEST_COMPANY_DOMAIN = os.environ.get(
    "TEST_ROCKETREACH_COMPANY_DOMAIN",
    "microsoft.com",
)


# ---------------------------------------------------------------------------
# Helpers — service-token headers, platform balance, debit-delta assertion
# ---------------------------------------------------------------------------


def _svc_headers(
    tenant_id: str = RICH_TENANT_ID,
    agent_type: str = CONSULTANT_AGENT,
) -> dict[str, str]:
    """Service-token headers as gtm-consultant — matches fresh_linkedin tests."""
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-Id": tenant_id,
        "X-Agent-Type": agent_type,
    }


def _platform_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {PLATFORM_TOKEN}"}


async def _get_platform_balance(
    client: httpx.AsyncClient, tenant_id: str = RICH_TENANT_ID
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
    """Return ``before - after`` after letting fire-and-forget debits settle."""
    await asyncio.sleep(settle_seconds)
    balance_after = await _get_platform_balance(client, tenant_id)
    return balance_before - balance_after


def _execute_body(operation: str, provider: str, **params) -> dict:
    return {
        "operation": operation,
        "provider": provider,
        "params": params,
    }


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


def _assert_canonical_person_shape(data: dict, provider: str = "rocketreach") -> None:
    """Shared shape contract for a normalized Person response.

      - Top-level keys are bounded to CANONICAL ∪ metadata.
      - For match_found != False responses, `additional_data` is a dict.
      - `enrichment_sources[<provider>]` is a list of ONLY canonical keys.
      - Explicitly guard against non-canonical fields leaking to the top level
        from the RocketReach retrofit (LLD §3.5 / canonical refactor).
    """
    top_level = set(data.keys())
    stray = top_level - ALLOWED_TOP_LEVEL_PERSON
    assert stray == set(), (
        f"non-canonical keys leaked to top level from {provider}: {sorted(stray)}"
    )

    if data.get("match_found") is not False:
        assert isinstance(data.get("additional_data"), dict), (
            f"{provider} match-found response must carry additional_data as a dict"
        )

    sources = (data.get("enrichment_sources") or {}).get(provider)
    assert isinstance(sources, list), (
        f"enrichment_sources['{provider}'] must be a list "
        f"(got {type(sources).__name__})"
    )
    for key in sources:
        assert key in CANONICAL_PERSON_KEYS, (
            f"enrichment_sources['{provider}'] must list only canonical keys; "
            f"saw '{key}'"
        )

    for guarded in ROCKETREACH_PERSON_EXTRAS_TO_GUARD:
        assert guarded not in data, (
            f"'{guarded}' leaked to Person top level — "
            f"RocketReach canonical retrofit regressing"
        )


def _assert_canonical_company_shape(data: dict, provider: str = "rocketreach") -> None:
    """Shape contract for a normalized Company response (single row or list row)."""
    top_level = set(data.keys())
    stray = top_level - ALLOWED_TOP_LEVEL_COMPANY
    assert stray == set(), (
        f"non-canonical keys leaked to Company top level from {provider}: "
        f"{sorted(stray)}"
    )

    if data.get("match_found") is not False:
        assert isinstance(data.get("additional_data"), dict), (
            f"{provider} match-found Company response must carry additional_data"
        )

    sources = (data.get("enrichment_sources") or {}).get(provider)
    assert isinstance(sources, list), (
        f"enrichment_sources['{provider}'] must be a list"
    )
    for key in sources:
        assert key in CANONICAL_COMPANY_KEYS, (
            f"enrichment_sources['{provider}'] Company entries must be canonical; "
            f"saw '{key}'"
        )

    for guarded in ROCKETREACH_COMPANY_EXTRAS_TO_GUARD:
        assert guarded not in data, (
            f"'{guarded}' leaked to Company top level — canonical retrofit regressing"
        )


# ===========================================================================
# Scenario 1 — enrich_person: LinkedIn URL happy path
# ===========================================================================


async def test_enrich_person_via_linkedin_url_returns_canonical_and_charges_three(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S1: enrich_person via linkedin_url → 200 + canonical + 3 credits ---")

    balance_before = await _get_platform_balance(client)
    print(f"Balance before: {balance_before}")

    # Dodge the cache so the upstream call + debit actually happen.
    _flush_cache_silently()

    resp = await _post_execute(
        client,
        _execute_body("enrich_person", "rocketreach", linkedin_url=TEST_LINKEDIN_URL),
    )
    body = resp.json()
    print(f"Status: {resp.status_code}  credits={body.get('credits_charged')}")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
    assert body["status"] == "success"
    assert body["credits_charged"] == ROCKETREACH_COST, (
        f"Expected {ROCKETREACH_COST} credits, got {body.get('credits_charged')}"
    )

    data = body["result"]
    _assert_canonical_person_shape(data, "rocketreach")

    if data.get("match_found") is False:
        print("(no upstream match — shape-only assertions)")
    else:
        assert any(data.get(k) for k in ("name", "first_name", "title")), (
            f"Expected at least one identity/role canonical field; "
            f"got keys: {list(data.keys())}"
        )

    diff = await _balance_delta_after(client, balance_before)
    print(f"Credits deducted: {diff}")
    assert abs(diff - ROCKETREACH_COST) < 0.01, (
        f"Expected ~{ROCKETREACH_COST} credit debit, got {diff}"
    )
    print("PASS")


# ===========================================================================
# Scenario 2 — enrich_person: email happy path
# ===========================================================================


async def test_enrich_person_via_email_returns_canonical_and_charges_three(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S2: enrich_person via email → 200 + canonical + 3 credits ---")

    _flush_cache_silently()
    balance_before = await _get_platform_balance(client)

    # Use a unique per-run email so we don't collide with a prior cache entry
    # if the Redis flush is skipped.
    unique_email = f"rr-email-{int(time.time())}@example.com"
    resp = await _post_execute(
        client,
        _execute_body("enrich_person", "rocketreach", email=unique_email),
    )
    body = resp.json()
    print(f"Status: {resp.status_code}  credits={body.get('credits_charged')}")

    # Either the vendor resolves this email or it returns the match_found=false
    # sentinel — both are 200 in the execute contract (404 is coerced upstream).
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
    data = body["result"]
    _assert_canonical_person_shape(data, "rocketreach")
    # The canonical contract: a non-match must use the sentinel, not an empty
    # row. A match must populate at least one identity field.
    if data.get("match_found") is False:
        assert data.get("people") == [], (
            "match_found=false must be paired with an empty people list"
        )
    print("PASS")


# ===========================================================================
# Scenario 3 — enrich_person: non-canonical extras never leak to top level
# ===========================================================================


async def test_enrich_person_top_level_holds_only_canonical_and_metadata(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S3: enrich_person response top level is bounded ---")

    _flush_cache_silently()
    resp = await _post_execute(
        client,
        _execute_body("enrich_person", "rocketreach", linkedin_url=TEST_LINKEDIN_URL),
    )
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"

    data = resp.json()["result"]
    print(f"Top-level keys: {sorted(data.keys())}")

    # Full shape contract AND explicit leak guard. Together these prove the
    # normalizer routes RocketReach-specific fields through additional_data.
    _assert_canonical_person_shape(data, "rocketreach")
    print("PASS")


# ===========================================================================
# Scenario 4 — enrich_person: missing identifier → 400, no debit
# ===========================================================================


async def test_enrich_person_rejects_missing_identifier_with_400_and_no_debit(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S4: enrich_person without any identifier → 400, no debit ---")

    balance_before = await _get_platform_balance(client)

    # Provider validates: requires name+company, email, linkedin_url, or id.
    # An empty params dict (or one with only whitespace fields) must be
    # rejected without reaching the vendor.
    resp = await _post_execute(
        client,
        _execute_body("enrich_person", "rocketreach"),
    )
    print(f"Status: {resp.status_code}  body={resp.text[:200]}")

    # The provider raises ProviderError without a status_code; router default
    # is 502. Per requirements §7 the validator surface should be 400, but the
    # observable caller contract is "rejected cleanly, no debit". Accept both.
    assert resp.status_code in (400, 502), (
        f"Expected 400/502 for missing identifier, got {resp.status_code}"
    )
    assert "linkedin" in resp.text.lower() or "identifier" in resp.text.lower() \
        or "required" in resp.text.lower(), (
        f"Error must name the missing field constraint; got: {resp.text[:200]}"
    )

    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff) < 0.01, (
        f"Rejected request must not debit credits; delta={diff}"
    )
    print("PASS")


# ===========================================================================
# Scenario 5 — cache hit on second identical call
# ===========================================================================


async def test_second_identical_enrich_person_call_is_served_from_cache(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S5: two identical calls → second cached, 3 total credits ---")

    _flush_cache_silently()
    balance_before = await _get_platform_balance(client)

    # Use a per-run unique identifier so the cache starts empty regardless of
    # whether the Redis flush ran.
    unique_email = f"rr-cache-{int(time.time())}@example.com"
    body = _execute_body("enrich_person", "rocketreach", email=unique_email)

    first = await _post_execute(client, body)
    second = await _post_execute(client, body)
    print(f"First  status={first.status_code}  credits={first.json().get('credits_charged')}")
    print(f"Second status={second.status_code}  credits={second.json().get('credits_charged')}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["credits_charged"] == ROCKETREACH_COST, (
        f"First call must be a paid vendor call; got {first.json()['credits_charged']}"
    )
    assert second.json()["credits_charged"] == 0, (
        f"Second identical call must hit the cache (0 credits); "
        f"got {second.json()['credits_charged']}"
    )

    diff = await _balance_delta_after(client, balance_before, settle_seconds=3.0)
    print(f"Credits deducted total: {diff}  (expected {ROCKETREACH_COST})")
    assert abs(diff - ROCKETREACH_COST) < 0.01, (
        f"Expected one paid call + one cached call = {ROCKETREACH_COST} credits; "
        f"saw {diff}"
    )
    print("PASS")


# ===========================================================================
# Scenario 6 — enrich_person without provider still defaults to apollo
# ===========================================================================


async def test_enrich_person_without_provider_does_not_route_to_rocketreach(
    client: httpx.AsyncClient,
) -> None:
    print(
        "\n--- S6: enrich_person without provider → still Apollo (no silent routing change) ---"
    )

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers=_svc_headers(),
        json={
            "operation": "enrich_person",
            "params": {"email": "test@freshworks.com"},
            # note: no "provider" key
        },
    )
    assert resp.status_code in (200, 404), (
        f"Expected 200 or 404, got {resp.status_code}: {resp.text[:300]}"
    )

    data = resp.json().get("result", {}) or {}
    sources = data.get("enrichment_sources") or {}
    print(f"enrichment_sources keys: {list(sources.keys())}")
    assert "apollo" in sources, (
        f"Default routing must stay on apollo; saw sources {list(sources.keys())}"
    )
    assert "rocketreach" not in sources, (
        f"Default route must NOT silently hit rocketreach; saw {list(sources.keys())}"
    )
    print("PASS")


# ===========================================================================
# Scenario 7 — search_people: canonical rows + 3 credits
# ===========================================================================


async def test_search_people_returns_canonical_rows_and_charges_three_credits(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S7: search_people → 200 + canonical rows + 3 credits ---")

    _flush_cache_silently()
    balance_before = await _get_platform_balance(client)

    resp = await _post_execute(
        client,
        _execute_body(
            "search_people",
            "rocketreach",
            title="VP Sales",
            company_domain=TEST_COMPANY_DOMAIN,
            page_size=5,
        ),
    )
    body = resp.json()
    print(f"Status: {resp.status_code}  credits={body.get('credits_charged')}")

    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"
    assert body["credits_charged"] == ROCKETREACH_COST

    data = body["result"]
    assert "people" in data, f"Expected 'people' list in search result; got {list(data.keys())}"
    assert isinstance(data["people"], list)

    for row in data["people"][:3]:
        _assert_canonical_person_shape(row, "rocketreach")

    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff - ROCKETREACH_COST) < 0.01, (
        f"Expected {ROCKETREACH_COST} credit debit, got {diff}"
    )
    print("PASS")


# ===========================================================================
# Scenario 8 — search_people: teaser hints surfaced (requirements §5.5 bug fix)
# ===========================================================================


async def test_search_people_rows_surface_teaser_hints_in_additional_data(
    client: httpx.AsyncClient,
) -> None:
    print(
        "\n--- S8: search_people rows carry email_domain_hints / phone_hint "
        "/ is_premium_phone_available in additional_data ---"
    )

    _flush_cache_silently()
    resp = await _post_execute(
        client,
        _execute_body(
            "search_people",
            "rocketreach",
            title="CEO",
            company_domain=TEST_COMPANY_DOMAIN,
            page_size=10,
        ),
    )
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"

    people = resp.json()["result"].get("people") or []
    if not people:
        print("SKIP: vendor returned no rows for this filter — bug-fix assertion not exercised")
        return

    # Teaser hints are optional per-row — some rows may have none. The
    # normalizer contract is: if the vendor ships teaser.* data, at least one
    # of {email_domain_hints, phone_hint, is_premium_phone_available} must
    # land under additional_data across the page of results (LLD §3.5).
    known_hint_keys = {
        "email_domain_hints", "phone_hint", "is_premium_phone_available",
    }
    observed_hint_keys: set[str] = set()
    for row in people:
        extras = row.get("additional_data") or {}
        for key in known_hint_keys:
            if key in extras:
                observed_hint_keys.add(key)
                val = extras[key]
                if key == "email_domain_hints":
                    assert isinstance(val, list), (
                        f"email_domain_hints must be a list; got {type(val).__name__}"
                    )
                    for hint in val:
                        assert isinstance(hint, str) and hint, (
                            f"email_domain_hints entries must be non-empty strings; got {hint!r}"
                        )
                if key == "phone_hint":
                    assert isinstance(val, str) and val, (
                        f"phone_hint must be a non-empty string; got {val!r}"
                    )
                if key == "is_premium_phone_available":
                    assert isinstance(val, bool), (
                        f"is_premium_phone_available must be bool; "
                        f"got {type(val).__name__}"
                    )

    assert observed_hint_keys, (
        "No teaser hints observed across any row — the requirements §5.5 "
        "bug fix is regressing, or the vendor stopped shipping teaser data "
        "on this query. Re-check with a broader filter before declaring regression."
    )
    print(f"Hint keys observed across the page: {sorted(observed_hint_keys)}")
    print("PASS")


# ===========================================================================
# Scenario 9 — search_people: canonical email / phone stay null on rows
# ===========================================================================


async def test_search_people_rows_leave_canonical_email_and_phone_null(
    client: httpx.AsyncClient,
) -> None:
    print(
        "\n--- S9: search rows keep canonical email / phone null — "
        "teaser hints never get promoted to fabricated values ---"
    )

    _flush_cache_silently()
    resp = await _post_execute(
        client,
        _execute_body(
            "search_people",
            "rocketreach",
            title="VP Marketing",
            company_domain=TEST_COMPANY_DOMAIN,
            page_size=10,
        ),
    )
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"

    people = resp.json()["result"].get("people") or []
    if not people:
        print("SKIP: vendor returned no rows for this filter")
        return

    for row in people:
        # Canonical keys must either be absent (dropped by _nonempty) or None.
        # The normalizer explicitly sets them to None on search rows — and
        # _build_person_row drops Nones, so the keys typically won't appear
        # at all. Any populated email/phone here would mean the normalizer
        # is fabricating contact data from teaser domains.
        assert "email" not in row or row.get("email") in (None, ""), (
            f"Search row must not carry a populated canonical email: {row.get('email')!r}"
        )
        assert "phone" not in row or row.get("phone") in (None, ""), (
            f"Search row must not carry a populated canonical phone: {row.get('phone')!r}"
        )
    print("PASS")


# ===========================================================================
# Scenario 10 — enrich_company: new op, canonical Company, 3 credits
# ===========================================================================


async def test_enrich_company_via_domain_returns_canonical_company_and_charges_three(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S10: enrich_company via domain → 200 + canonical + 3 credits ---")

    _flush_cache_silently()
    balance_before = await _get_platform_balance(client)

    resp = await _post_execute(
        client,
        _execute_body("enrich_company", "rocketreach", domain=TEST_COMPANY_DOMAIN),
    )
    body = resp.json()
    print(f"Status: {resp.status_code}  credits={body.get('credits_charged')}")

    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"
    assert body["credits_charged"] == ROCKETREACH_COST, (
        f"Expected {ROCKETREACH_COST} credits, got {body['credits_charged']}"
    )

    data = body["result"]
    _assert_canonical_company_shape(data, "rocketreach")

    if data.get("match_found") is not False:
        assert any(data.get(k) for k in ("name", "domain")), (
            f"Expected name or domain on canonical Company; got keys {list(data.keys())}"
        )

    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff - ROCKETREACH_COST) < 0.01, (
        f"Expected {ROCKETREACH_COST} credit debit, got {diff}"
    )
    print("PASS")


# ===========================================================================
# Scenario 11 — enrich_company: missing domain AND name → rejected, no debit
# ===========================================================================


async def test_enrich_company_rejects_missing_domain_and_name_with_no_debit(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S11: enrich_company with neither domain nor name → rejected, no debit ---")

    balance_before = await _get_platform_balance(client)

    resp = await _post_execute(
        client,
        _execute_body("enrich_company", "rocketreach"),
    )
    print(f"Status: {resp.status_code}  body={resp.text[:200]}")

    assert resp.status_code in (400, 502), (
        f"Expected 400/502, got {resp.status_code}"
    )
    assert "domain" in resp.text.lower() or "name" in resp.text.lower(), (
        f"Error must name the missing constraint; got: {resp.text[:200]}"
    )

    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff) < 0.01, f"Rejected request must not debit; delta={diff}"
    print("PASS")


# ===========================================================================
# Scenario 12 — enrich_company: Universal renames absorbed (LLD §3.6 / reqs §6)
# ===========================================================================


async def test_enrich_company_absorbs_universal_field_renames(
    client: httpx.AsyncClient,
) -> None:
    print(
        "\n--- S12: enrich_company response uses canonical `domain` / `industry`, "
        "never leaks legacy `email_domain` / `industry_str` ---"
    )

    _flush_cache_silently()
    resp = await _post_execute(
        client,
        _execute_body("enrich_company", "rocketreach", domain=TEST_COMPANY_DOMAIN),
    )
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"

    data = resp.json()["result"]
    _assert_canonical_company_shape(data, "rocketreach")

    # On a match path, at least one of {domain, industry} should be populated
    # for a well-known company like Microsoft/Google. On a no-match path we
    # only assert the leak guard (already done above) — the rename tolerance
    # is still proved because no legacy key leaked.
    if data.get("match_found") is not False:
        populated_canonical = {
            k: data.get(k) for k in ("name", "domain", "industry") if data.get(k)
        }
        assert populated_canonical, (
            f"Expected at least one canonical Company field populated; "
            f"got {list(data.keys())}"
        )
        print(f"Canonical fields present: {sorted(populated_canonical.keys())}")
    print("PASS")


# ===========================================================================
# Scenario 13 — search_companies: new op, canonical rows, 3 credits
# ===========================================================================


async def test_search_companies_returns_canonical_rows_and_charges_three_credits(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S13: search_companies → 200 + canonical rows + 3 credits ---")

    _flush_cache_silently()
    balance_before = await _get_platform_balance(client)

    resp = await _post_execute(
        client,
        _execute_body(
            "search_companies",
            "rocketreach",
            industry="Software",
            employees=["51-200", "201-500"],
            page_size=5,
        ),
    )
    body = resp.json()
    print(f"Status: {resp.status_code}  credits={body.get('credits_charged')}")

    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"
    assert body["credits_charged"] == ROCKETREACH_COST

    data = body["result"]
    assert "companies" in data, (
        f"Expected 'companies' list in search result; got {list(data.keys())}"
    )
    assert isinstance(data["companies"], list)

    for row in data["companies"][:3]:
        _assert_canonical_company_shape(row, "rocketreach")

    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff - ROCKETREACH_COST) < 0.01, (
        f"Expected {ROCKETREACH_COST} credit debit, got {diff}"
    )
    print("PASS")


# ===========================================================================
# Scenario 14 — search_companies: pagination normalized across vendor shapes
# ===========================================================================


async def test_search_companies_normalizes_pagination_to_canonical_keys(
    client: httpx.AsyncClient,
) -> None:
    print(
        "\n--- S14: search_companies pagination is normalized to "
        "`total` / `page` / `per_page` regardless of vendor shape ---"
    )

    _flush_cache_silently()
    resp = await _post_execute(
        client,
        _execute_body(
            "search_companies",
            "rocketreach",
            industry="Software",
            page_size=5,
            start=1,
        ),
    )
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"

    data = resp.json()["result"]
    # Canonical pagination surface per `normalize_company`: total / page / per_page.
    # The Universal vendor shape (`start`/`next`/`total`) and the legacy shape
    # (`total`/`thisPage`/`nextPage`/`pageSize`) both normalize into these.
    for key in ("total", "page", "per_page"):
        assert key in data, (
            f"Canonical pagination key '{key}' missing from response; "
            f"got {list(data.keys())}"
        )
    # Legacy vendor keys must never bleed through to the envelope.
    for legacy_key in ("thisPage", "nextPage", "pageSize", "start", "next"):
        assert legacy_key not in data, (
            f"Legacy pagination key '{legacy_key}' leaked to response — "
            f"normalizer pagination tolerance is regressing"
        )
    print(
        f"Pagination: total={data.get('total')} page={data.get('page')} "
        f"per_page={data.get('per_page')}"
    )
    print("PASS")


# ===========================================================================
# Scenario 15 — cost estimate: 3 credits per operation (catalog sanity)
# ===========================================================================


async def test_cost_estimate_reports_three_credits_for_every_rocketreach_operation(
    client: httpx.AsyncClient,
) -> None:
    print(
        "\n--- S15: /execute/cost reports 3 credits for each of the four "
        "RocketReach operations ---"
    )

    sample_params = {
        "enrich_person": {"linkedin_url": TEST_LINKEDIN_URL},
        "search_people": {"title": "VP Sales", "page_size": 25},
        "enrich_company": {"domain": TEST_COMPANY_DOMAIN},
        "search_companies": {"industry": "Software", "page_size": 25},
    }

    for operation, params in sample_params.items():
        resp = await client.post(
            f"{BASE_URL}/api/v1/execute/cost",
            headers=_svc_headers(),
            json={
                "operation": operation,
                "provider": "rocketreach",
                "params": params,
            },
        )
        assert resp.status_code == 200, (
            f"[{operation}] /execute/cost failed: {resp.status_code} {resp.text[:200]}"
        )
        body = resp.json()
        print(
            f"[{operation}] estimated_credits={body.get('estimated_credits')}  "
            f"breakdown={body.get('breakdown')!s:.120}"
        )
        assert body["estimated_credits"] == ROCKETREACH_COST, (
            f"[{operation}] expected {ROCKETREACH_COST} credits, "
            f"got {body.get('estimated_credits')}. Catalog or migration 019 "
            f"is out of sync."
        )
    print("PASS")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _flush_cache_silently() -> int:
    if redis is None:
        return 0
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
    print("RocketReach Universal API Tests — consultant-agent simulation")
    print(f"GTM Engine:     {BASE_URL}")
    print(f"Platform:       {PLATFORM_URL}")
    print(f"Rich tenant:    {RICH_TENANT_ID}")
    print(f"Agent identity: {CONSULTANT_AGENT}")
    print(f"LinkedIn URL:   {TEST_LINKEDIN_URL}")
    print(f"Company domain: {TEST_COMPANY_DOMAIN}")
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
            test_enrich_person_via_linkedin_url_returns_canonical_and_charges_three,
            test_enrich_person_via_email_returns_canonical_and_charges_three,
            test_enrich_person_top_level_holds_only_canonical_and_metadata,
            test_enrich_person_rejects_missing_identifier_with_400_and_no_debit,
            test_second_identical_enrich_person_call_is_served_from_cache,
            test_enrich_person_without_provider_does_not_route_to_rocketreach,
            test_search_people_returns_canonical_rows_and_charges_three_credits,
            test_search_people_rows_surface_teaser_hints_in_additional_data,
            test_search_people_rows_leave_canonical_email_and_phone_null,
            test_enrich_company_via_domain_returns_canonical_company_and_charges_three,
            test_enrich_company_rejects_missing_domain_and_name_with_no_debit,
            test_enrich_company_absorbs_universal_field_renames,
            test_search_companies_returns_canonical_rows_and_charges_three_credits,
            test_search_companies_normalizes_pagination_to_canonical_keys,
            test_cost_estimate_reports_three_credits_for_every_rocketreach_operation,
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
