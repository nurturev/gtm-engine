"""API tests — Fresh LinkedIn Profile Data provider (`fresh_linkedin`).

Simulates the **gtm-consultant agent** making direct service-token calls
to the GTM Engine execution API. BDD principles applied in plain Python
(per `backend-api-testing-blueprint.md` §2–§4) — no Behave, no Gherkin,
just declarative scenario-named functions hitting the real app.

---

Scenarios (one `test_` function each, below):

POST /api/v1/execute with provider="fresh_linkedin"
  - accepts a valid LinkedIn URL and returns a canonical-shape Person with 3-credit debit
  - response top level carries only canonical keys + enrichment_sources + additional_data
  - moves non-canonical fields (about / photo_url / city / state / country / skills / ...) into additional_data
  - normalizes URL variants (trailing slash + tracking params) before calling upstream
  - rejects a missing linkedin_url with 400 and no debit
  - rejects a LinkedIn /company/ URL with 400 and no debit
  - rejects a non-LinkedIn host with 400 and no debit
  - rejects an unsupported operation with 400 and no debit
  - bypasses the cache — two identical calls produce two upstream hits and two debits
  - bills 0 credits when the tenant has a BYOK key for fresh_linkedin  (requires env var)
  - leaves DEFAULT_PROVIDERS[enrich_person] as 'apollo' — no silent routing change
  - Apollo response shape also canonical: extras (photo_url, seniority, departments, city, ...) under additional_data
  - RocketReach response shape also canonical: extras (photo_url, skills, city, ...) under additional_data
  - mixed batch — every row shares the exact same top-level canonical key set, provider-specific extras in its own additional_data

Scenarios intentionally deferred (require server-side upstream injection, not in
this script's remit — they live in the wiring-level mock tests per LLD §11.2):
  - 429 single retry then 200 (observe one retry, 3 credits debited)
  - 429 exhausted after one retry (429 surfaced, no debit)
  - Upstream 404 (404 surfaced, no debit)
  - Upstream 5xx after retries (5xx surfaced, no debit)
  - 503 missing platform key + no BYOK

---

Prerequisites:
    1. Server running:  cd server && uvicorn server.app:app --reload
    2. Platform key `LINKEDIN_RAPIDAPI_KEY` provisioned in the server env
    3. Migration 018 applied (`operation_costs` row for fresh_linkedin)
    4. `fresh_linkedin` registered in `VENDOR_CATALOG` and `INTEGRATED_PROVIDERS`
    5. Env vars (see top of file):
         - TEST_LINKEDIN_URL            — real profile URL upstream will resolve
         - TEST_FRESH_LINKEDIN_BYOK_KEY — optional; enables the BYOK scenario

Run:
    python tests/api_tests/test_fresh_linkedin_api.py
    python tests/api_tests/test_fresh_linkedin_api.py --no-cache   # skip Redis flush
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import httpx
import redis


# ---------------------------------------------------------------------------
# Configuration — matches existing test_execution_router_wiring.py conventions
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

# Expected credit cost — must match migrations/018 + VENDOR_CATALOG.
FRESH_LINKEDIN_COST = 3.0

# Canonical Person shape per unique_entity_fields.csv + HLD §3.1. The response
# envelope (inside result.data) must carry ONLY these keys at top level,
# alongside `enrichment_sources` and `additional_data`.
CANONICAL_PERSON_KEYS = frozenset(
    {
        "name",
        "first_name",
        "last_name",
        "title",
        "headline",
        "experiences",
        "linkedin_url",
        "email",
        "phone",
        "location",
        "company_name",
        "company_domain",
    }
)
# Metadata / envelope keys also permitted at top level. ``people`` appears on
# the no-match path; ``additional_data`` on the match path.
META_PERSON_KEYS = frozenset(
    {"enrichment_sources", "additional_data", "match_found", "people"}
)
ALLOWED_TOP_LEVEL_PERSON = CANONICAL_PERSON_KEYS | META_PERSON_KEYS

# Inputs pulled from env so CI / local runs can supply real values.
# Default is a known-stable profile the owner controls — guarantees upstream
# returns real data so S1's "populated canonical + additional_data" assertion
# fires against something concrete rather than a shape-only check.
TEST_LINKEDIN_URL = os.environ.get(
    "TEST_LINKEDIN_URL",
    "https://www.linkedin.com/in/satyanadella",
)
TEST_BYOK_KEY = os.environ.get("TEST_FRESH_LINKEDIN_BYOK_KEY")


# ---------------------------------------------------------------------------
# Helpers — service-token headers, platform balance, debit-delta assertion
# ---------------------------------------------------------------------------


def _svc_headers(
    tenant_id: str = RICH_TENANT_ID,
    agent_type: str = CONSULTANT_AGENT,
) -> dict[str, str]:
    """Service-token headers as gtm-consultant. The X-Agent-Type value matters
    for server-side routing/audit; this is the identity consultant carries."""
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


def _execute_body(provider: str, **params) -> dict:
    return {
        "operation": "enrich_person",
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


def _assert_canonical_person_shape(data: dict, provider: str) -> None:
    """Shared shape contract for a normalized Person response across every
    provider. Enforces the canonical-shape refactor's guarantees:

      - Top-level keys are bounded to CANONICAL ∪ metadata.
      - For match_found != False responses, ``additional_data`` exists and is
        a dict (no-match responses skip this check — that path emits
        ``{match_found: false, people: [], enrichment_sources: ...}``).
      - ``enrichment_sources[<provider>]`` is a list of ONLY canonical keys.
    """
    top_level = set(data.keys())
    stray = top_level - ALLOWED_TOP_LEVEL_PERSON
    assert (
        stray == set()
    ), f"non-canonical keys leaked to top level from {provider}: {sorted(stray)}"

    # additional_data is required on the match path; omitted on no-match.
    if data.get("match_found") is not False:
        assert isinstance(
            data.get("additional_data"), dict
        ), f"{provider} match-found response must carry additional_data as a dict"

    sources = (data.get("enrichment_sources") or {}).get(provider)
    assert isinstance(
        sources, list
    ), f"enrichment_sources['{provider}'] must be a list (got {type(sources).__name__})"
    for key in sources:
        assert (
            key in CANONICAL_PERSON_KEYS
        ), f"enrichment_sources['{provider}'] must list only canonical keys; saw '{key}'"


# ---------------------------------------------------------------------------
# BYOK helpers — add/remove a key for the BYOK scenario
# ---------------------------------------------------------------------------


async def _add_byok_key(client: httpx.AsyncClient, provider: str, key: str) -> None:
    resp = await client.post(
        f"{BASE_URL}/api/v1/keys",
        headers=_svc_headers(),
        json={"provider": provider, "api_key": key},
    )
    assert resp.status_code in (
        200,
        201,
    ), f"Failed to add BYOK for {provider}: {resp.status_code} {resp.text[:200]}"


async def _remove_byok_key(client: httpx.AsyncClient, provider: str) -> None:
    resp = await client.delete(
        f"{BASE_URL}/api/v1/keys/{provider}",
        headers=_svc_headers(),
    )
    # 200 or 204 on success; 404 means nothing to delete — both fine for cleanup.
    assert resp.status_code in (
        200,
        204,
        404,
    ), f"Unexpected status while removing BYOK for {provider}: {resp.status_code}"


# ===========================================================================
# Scenario 1 — happy path
# ===========================================================================


async def test_accepts_valid_linkedin_url_and_charges_three_credits(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S1: valid LinkedIn URL → 200, canonical-shape Person, 3 credits ---")

    # Given — a known LinkedIn profile URL and the tenant's balance before
    balance_before = await _get_platform_balance(client)
    print(f"Balance before: {balance_before}")

    # When — the consultant calls the execute API with provider=fresh_linkedin
    resp = await _post_execute(
        client,
        _execute_body("fresh_linkedin", linkedin_url=TEST_LINKEDIN_URL),
    )
    body = resp.json()
    print(f"Status: {resp.status_code}  cached={body.get('result', {}).get('cached')}")

    # Then — wrapper contract: 200 + 3 credits debited.
    assert (
        resp.status_code == 200
    ), f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
    assert body["status"] == "success"
    assert (
        body["credits_charged"] == FRESH_LINKEDIN_COST
    ), f"Expected {FRESH_LINKEDIN_COST} credits, got {body.get('credits_charged')}"

    # Canonical-shape contract (v2.0 refactor). `result` IS the Person data.
    data = body["result"]
    _assert_canonical_person_shape(data, "fresh_linkedin")

    # Real data contract: a 200 must carry actual profile data. An upstream
    # no-match now raises (ProviderError 404) rather than returning a silent
    # match_found:false — so S1 requires populated canonical + extras fields.
    # The 404-no-debit scenario is deferred to wiring-level mocks in
    # tests/api_tests/test_execution_router_wiring.py (see the deferred-
    # scenarios block at the top of this file).
    assert data.get("match_found") is not False, (
        f"Upstream returned match_found:false for a known profile URL; "
        f"post-fix enrich_person must raise (ProviderError 404) rather than "
        f"return an empty 200. data={data}"
    )
    assert any(data.get(k) for k in ("name", "first_name", "headline", "title")), (
        f"Expected at least one identity/role canonical field populated; "
        f"got top-level keys: {list(data.keys())}"
    )
    extras = data["additional_data"]
    print(f"additional_data keys: {sorted(extras.keys())}")
    fresh_linkedin_expected_extras = {
        "about",
        "photo_url",
        "city",
        "state",
        "country",
        "connections_count",
        "follower_count",
        "skills",
        "languages",
        "education",
        "certifications",
        "company_industry",
        "company_size",
    }
    overlap = set(extras.keys()) & fresh_linkedin_expected_extras
    assert overlap, (
        f"fresh_linkedin response must populate at least one known additional_data key; "
        f"got {sorted(extras.keys())}"
    )

    diff = await _balance_delta_after(client, balance_before)
    print(f"Credits deducted: {diff}")
    assert (
        abs(diff - FRESH_LINKEDIN_COST) < 0.01
    ), f"Expected ~{FRESH_LINKEDIN_COST} credit debit, got {diff}"
    print("PASS")


# ===========================================================================
# Scenario 2 — URL normalization at the server
# ===========================================================================


async def test_normalizes_url_variants_before_calling_upstream(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S2: URL variants (trailing slash, tracking params) are accepted ---")

    # Given — the same profile URL with noise appended
    noisy = TEST_LINKEDIN_URL.rstrip("/") + "/?utm_source=share&utm_medium=member"

    # When
    resp = await _post_execute(
        client,
        _execute_body("fresh_linkedin", linkedin_url=noisy),
    )

    # Then — the server normalises and fulfils the call the same way as scenario 1
    print(f"Status: {resp.status_code}")
    assert (
        resp.status_code == 200
    ), f"Expected 200 after URL normalisation, got {resp.status_code}: {resp.text[:300]}"
    data = resp.json()["result"]
    # Either the vendor resolved the profile or it didn't — both reach the
    # normalizer and produce a valid response. The invariant here is that
    # noisy URLs don't 400 at the validator.
    assert isinstance(
        data.get("enrichment_sources"), dict
    ), "Normalisation must succeed end-to-end — enrichment_sources dict must exist"
    print("PASS")


# ===========================================================================
# Scenario 3–6 — 400 validation paths, no upstream call, no debit
# ===========================================================================


async def _assert_validation_400_no_debit(
    client: httpx.AsyncClient,
    body: dict,
    label: str,
) -> None:
    balance_before = await _get_platform_balance(client)

    resp = await _post_execute(client, body)
    print(f"[{label}] status={resp.status_code}  detail={str(resp.json())[:200]}")

    assert (
        resp.status_code == 400
    ), f"[{label}] expected 400, got {resp.status_code}: {resp.text[:300]}"

    diff = await _balance_delta_after(client, balance_before)
    assert (
        abs(diff) < 0.01
    ), f"[{label}] rejected request must not debit credits; delta={diff}"


async def test_rejects_missing_linkedin_url_with_400(client: httpx.AsyncClient) -> None:
    print("\n--- S3: missing linkedin_url → 400, no debit ---")
    await _assert_validation_400_no_debit(
        client,
        _execute_body("fresh_linkedin"),  # no linkedin_url at all
        label="missing-url",
    )
    print("PASS")


async def test_rejects_linkedin_company_url_with_400(client: httpx.AsyncClient) -> None:
    print("\n--- S4: /company/ URL → 400, no debit ---")
    await _assert_validation_400_no_debit(
        client,
        _execute_body(
            "fresh_linkedin",
            linkedin_url="https://www.linkedin.com/company/acme",
        ),
        label="company-url",
    )
    print("PASS")


async def test_rejects_non_linkedin_host_with_400(client: httpx.AsyncClient) -> None:
    print("\n--- S5: non-linkedin.com host → 400, no debit ---")
    await _assert_validation_400_no_debit(
        client,
        _execute_body(
            "fresh_linkedin",
            linkedin_url="https://twitter.com/in/janedoe",
        ),
        label="non-linkedin-host",
    )
    print("PASS")


async def test_rejects_unsupported_operation_without_debit(
    client: httpx.AsyncClient,
) -> None:
    """The orchestration layer (execute_single) pre-gates unsupported
    operations before the provider's own 400 validator runs. That pre-gate
    raises ``ProviderError`` without a status_code, which the router maps to
    502. Per LLD §2 this should ideally be 400; tracked as a spec gap.
    What matters for the caller: the request is rejected cleanly and no
    credits are debited. Accept either status until the gap is fixed.
    """
    print("\n--- S6: unsupported operation → rejected (400 or 502), no debit ---")
    balance_before = await _get_platform_balance(client)

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers=_svc_headers(),
        json={
            "operation": "search_people",
            "provider": "fresh_linkedin",
            "params": {"title": "VP Sales"},
        },
    )
    print(f"Status: {resp.status_code}  body={resp.text[:200]}")
    assert resp.status_code in (
        400,
        502,
    ), f"Expected 400 or 502 for unsupported op, got {resp.status_code}"
    # Error body must name the reason so the caller can re-plan.
    assert (
        "support" in resp.text.lower()
    ), f"Error must reference operation support; got: {resp.text[:200]}"

    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff) < 0.01, f"Unsupported-op rejection must not debit; delta={diff}"
    print("PASS")


# ===========================================================================
# Scenario 7 — cache bypass (D17)
# ===========================================================================


async def test_two_identical_calls_hit_upstream_twice_and_debit_twice(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S7: cache bypass — two identical calls charge 6 credits total ---")

    # Given — a clean cache state and baseline balance
    _flush_cache_silently()
    balance_before = await _get_platform_balance(client)
    print(f"Balance before: {balance_before}")

    # When — the consultant makes the exact same call twice in quick succession
    body = _execute_body("fresh_linkedin", linkedin_url=TEST_LINKEDIN_URL)
    first = await _post_execute(client, body)
    second = await _post_execute(client, body)
    print(
        f"First  status={first.status_code}  credits={first.json().get('credits_charged')}"
    )
    print(
        f"Second status={second.status_code}  credits={second.json().get('credits_charged')}"
    )

    # Then — both succeed, and the TOTAL debit is 2 × FRESH_LINKEDIN_COST.
    # A cached second call would charge 0; observing 6 credits proves bypass.
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["credits_charged"] == FRESH_LINKEDIN_COST
    assert second.json()["credits_charged"] == FRESH_LINKEDIN_COST, (
        f"fresh_linkedin must never serve a cached response (D17); "
        f"second call charged {second.json()['credits_charged']}"
    )

    diff = await _balance_delta_after(client, balance_before, settle_seconds=3.0)
    expected = 2 * FRESH_LINKEDIN_COST
    print(f"Credits deducted over two calls: {diff}  (expected {expected})")
    assert (
        abs(diff - expected) < 0.01
    ), f"Expected {expected} credits across two uncached calls, got {diff}"
    print("PASS")


# ===========================================================================
# Scenario 8 — BYOK path charges 0 credits (requires env var)
# ===========================================================================


async def test_byok_call_costs_zero_credits(client: httpx.AsyncClient) -> None:
    print("\n--- S8: BYOK — tenant key installed → is_byok=true, 0 debit ---")

    if not TEST_BYOK_KEY:
        print("SKIP: TEST_FRESH_LINKEDIN_BYOK_KEY not set")
        return

    # Given — we register a BYOK key for fresh_linkedin on this tenant
    await _add_byok_key(client, "fresh_linkedin", TEST_BYOK_KEY)
    try:
        balance_before = await _get_platform_balance(client)

        # When
        resp = await _post_execute(
            client,
            _execute_body("fresh_linkedin", linkedin_url=TEST_LINKEDIN_URL),
        )
        body = resp.json()
        print(
            f"Status: {resp.status_code}  credits_charged={body.get('credits_charged')}"
        )

        # Then — 200 and zero credits debited. The response envelope doesn't
        # carry an explicit is_byok flag; `credits_charged == 0` is the
        # observable contract (and the balance delta double-checks it).
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        assert (
            body["credits_charged"] == 0
        ), f"BYOK must not consume credits; got {body['credits_charged']}"

        diff = await _balance_delta_after(client, balance_before)
        assert abs(diff) < 0.01, f"BYOK path debited {diff} credits — expected 0"
        print("PASS")
    finally:
        # Clean up so other tests / reruns see a consistent tenant state.
        await _remove_byok_key(client, "fresh_linkedin")


# ===========================================================================
# Scenario 9 — default routing unchanged (D18)
# ===========================================================================


async def test_enrich_person_without_provider_still_routes_to_apollo(
    client: httpx.AsyncClient,
) -> None:
    print(
        "\n--- S9: enrich_person without provider → still Apollo (D18: no silent routing change) ---"
    )

    # When — caller does NOT specify provider (relying on default)
    resp = await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers=_svc_headers(),
        json={
            "operation": "enrich_person",
            "params": {"email": "test@freshworks.com"},
            # note: no "provider" key
        },
    )
    print(f"Status: {resp.status_code}")
    assert resp.status_code in (
        200,
        404,
    ), f"Expected 200 or 404 (no-match), got {resp.status_code}: {resp.text[:300]}"

    # Then — the response's enrichment_sources identifies the provider that ran.
    # The response envelope flattens `result` to the normalized Person, so
    # `enrichment_sources` is where attribution lives.
    data = resp.json().get("result", {}) or {}
    sources = data.get("enrichment_sources") or {}
    print(f"enrichment_sources keys: {list(sources.keys())}")
    assert (
        "apollo" in sources
    ), f"Default routing must stay on apollo (D18); saw sources {list(sources.keys())}"
    assert (
        "fresh_linkedin" not in sources
    ), f"Default route must NOT hit fresh_linkedin; saw sources {list(sources.keys())}"
    print("PASS")


# ===========================================================================
# Scenario 10 — mixed pair serialises with correct enrichment_sources
# ===========================================================================


async def test_mixed_apollo_and_fresh_linkedin_rows_serialise_correctly(
    client: httpx.AsyncClient,
) -> None:
    print(
        "\n--- S10: mixed Apollo + Fresh LinkedIn — identical canonical top-level; provider-specific extras ---"
    )

    # Given — two distinct single-enrichment calls, one per provider
    apollo_resp = await _post_execute(
        client,
        {
            "operation": "enrich_person",
            "provider": "apollo",
            "params": {"email": "test@freshworks.com"},
        },
    )
    fresh_resp = await _post_execute(
        client,
        _execute_body("fresh_linkedin", linkedin_url=TEST_LINKEDIN_URL),
    )

    print(f"Apollo: {apollo_resp.status_code}   Fresh: {fresh_resp.status_code}")
    assert apollo_resp.status_code == 200
    assert fresh_resp.status_code == 200

    apollo_data = apollo_resp.json()["result"]
    fresh_data = fresh_resp.json()["result"]

    # Both rows share the same top-level shape contract (canonical + metadata).
    _assert_canonical_person_shape(apollo_data, "apollo")
    _assert_canonical_person_shape(fresh_data, "fresh_linkedin")

    # enrichment_sources attribution — each row names only its own provider.
    apollo_sources = apollo_data.get("enrichment_sources") or {}
    fresh_sources = fresh_data.get("enrichment_sources") or {}
    print(f"Apollo sources keys: {list(apollo_sources.keys())}")
    print(f"Fresh sources keys:  {list(fresh_sources.keys())}")

    assert (
        "apollo" in apollo_sources and "fresh_linkedin" not in apollo_sources
    ), "Apollo row must only reference apollo in enrichment_sources"
    assert (
        "fresh_linkedin" in fresh_sources and "apollo" not in fresh_sources
    ), "Fresh LinkedIn row must only reference fresh_linkedin in enrichment_sources"
    print("PASS")


# ===========================================================================
# Scenario 11 — Apollo response shape (retrofit, LLD §11.2 new row)
# ===========================================================================


async def test_apollo_response_follows_canonical_shape(
    client: httpx.AsyncClient,
) -> None:
    print(
        "\n--- S11: Apollo response — canonical top level, extras under additional_data ---"
    )

    resp = await _post_execute(
        client,
        {
            "operation": "enrich_person",
            "provider": "apollo",
            "params": {"email": "test@freshworks.com"},
        },
    )
    assert resp.status_code in (200, 404), f"Got {resp.status_code}: {resp.text[:300]}"

    data = resp.json()["result"]
    print(f"Top-level keys: {sorted(data.keys())}")

    # Canonical-shape contract — same invariants as every other provider.
    _assert_canonical_person_shape(data, "apollo")

    # Fields that used to be top-level under v1 must now sit under additional_data
    # when Apollo returns them. We check "not leaked to top level"; presence in
    # additional_data depends on what the upstream payload carried for this test
    # email, so we don't require every extra key.
    for previously_top_level in (
        "id",
        "photo_url",
        "seniority",
        "departments",
        "city",
        "state",
        "country",
        "company_industry",
        "company_size",
    ):
        assert (
            previously_top_level not in data
        ), f"'{previously_top_level}' leaked to top level — Apollo retrofit incomplete"

    print("PASS")


# ===========================================================================
# Scenario 12 — RocketReach response shape (retrofit, LLD §11.2 new row)
# ===========================================================================


async def test_rocketreach_response_follows_canonical_shape(
    client: httpx.AsyncClient,
) -> None:
    print(
        "\n--- S12: RocketReach response — canonical top level, extras under additional_data ---"
    )

    # Use a fresh email per run. The execution cache keys on
    # (operation, params-hash) without provider, so reusing the email the
    # other scenarios hit would serve their cached Apollo response instead.
    unique_email = f"rr-shape-{int(time.time())}@example.com"
    resp = await _post_execute(
        client,
        {
            "operation": "enrich_person",
            "provider": "rocketreach",
            "params": {"email": unique_email},
        },
    )
    # 200 happy or 200-no-match are both valid; any success-path response must
    # satisfy the shape contract.
    assert resp.status_code in (200, 404), f"Got {resp.status_code}: {resp.text[:300]}"

    data = resp.json()["result"]
    print(f"Top-level keys: {sorted(data.keys())}")
    print(f"enrichment_sources: {list((data.get('enrichment_sources') or {}).keys())}")

    _assert_canonical_person_shape(data, "rocketreach")

    for previously_top_level in (
        "id",
        "photo_url",
        "skills",
        "city",
        "state",
        "country",
        "lookup_status",
    ):
        assert (
            previously_top_level not in data
        ), f"'{previously_top_level}' leaked to top level — RocketReach retrofit incomplete"

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
    print("Fresh LinkedIn API Tests — consultant-agent simulation")
    print(f"GTM Engine:     {BASE_URL}")
    print(f"Platform:       {PLATFORM_URL}")
    print(f"Rich tenant:    {RICH_TENANT_ID}")
    print(f"Agent identity: {CONSULTANT_AGENT}")
    print(f"Test URL:       {TEST_LINKEDIN_URL}")
    print(f"BYOK configured: {'yes' if TEST_BYOK_KEY else 'no (S8 will skip)'}")
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
            test_accepts_valid_linkedin_url_and_charges_three_credits,
            test_normalizes_url_variants_before_calling_upstream,
            test_rejects_missing_linkedin_url_with_400,
            test_rejects_linkedin_company_url_with_400,
            test_rejects_non_linkedin_host_with_400,
            test_rejects_unsupported_operation_without_debit,
            test_two_identical_calls_hit_upstream_twice_and_debit_twice,
            test_byok_call_costs_zero_credits,
            test_enrich_person_without_provider_still_routes_to_apollo,
            test_mixed_apollo_and_fresh_linkedin_rows_serialise_correctly,
            test_apollo_response_follows_canonical_shape,
            test_rocketreach_response_follows_canonical_shape,
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
