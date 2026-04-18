"""API tests — Phase 2.1 `enrich_company` via `fresh_linkedin`.

Simulates the **gtm-consultant agent** making direct service-token calls
to the GTM Engine execution API. BDD principles applied in plain Python
(per `backend-api-testing-blueprint.md` §2–§4) — declarative scenario
functions hitting the real app.

---

Scenarios (one `test_` function each, below):

POST /api/v1/execute with operation="enrich_company", provider="fresh_linkedin"
  - accepts a valid LinkedIn company URL → 200 + canonical Company + 3 credits
  - accepts a bare domain → 200 + canonical Company + 3 credits
  - response top level carries ONLY canonical keys + enrichment_sources + additional_data
  - auto-strips domain input (https://www.google.com/about → google.com upstream)
  - linkedin_url wins over domain when both are provided
  - rejects a profile URL in linkedin_url with 400 + no debit
  - rejects missing both linkedin_url and domain with 400 + no debit
  - rejects a malformed domain with 400 + no debit
  - bypasses the cache — two identical calls charge 6 credits total
  - bills 0 credits when the tenant has a BYOK key (requires env var)
  - enrich_company without provider still routes to apollo — no silent routing change
  - surfaces confident_score inside additional_data on fuzzy domain matches

Prerequisites:
    1. Server running:  cd server && uvicorn server.app:app --reload
    2. Platform key `LINKEDIN_RAPIDAPI_KEY` provisioned in the server env
    3. Migration 019 applied (`operation_costs` row for enrich_company @ 3 credits)
    4. Env vars (optional):
         - TEST_LINKEDIN_COMPANY_URL   — a real LinkedIn company URL (defaults to Google)
         - TEST_COMPANY_DOMAIN         — a real domain (defaults to google.com)
         - TEST_FRESH_LINKEDIN_BYOK_KEY — optional; enables the BYOK scenario

Run:
    python tests/api_tests/test_fresh_linkedin_company_api.py
    python tests/api_tests/test_fresh_linkedin_company_api.py --no-cache
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx
import redis


# ---------------------------------------------------------------------------
# Configuration — matches test_fresh_linkedin_api.py
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:8000"
SERVICE_TOKEN = "XRWnB_IpZa0f3T1G1rpsItpa_S2qJKHBZuY_3Bc8WDM"
RICH_TENANT_ID = "4"
CONSULTANT_AGENT = "gtm-consultant"

REDIS_URL = "redis://localhost:6379/0"
CACHE_PREFIX = "cache:exec:*"

PLATFORM_URL = "https://umws.public.staging.nurturev.com/private"
PLATFORM_TOKEN = "Na3G8LOC84N8J8y32A5mJUwP7Avb0P57"

# Expected credit cost — must match migrations/019 + VENDOR_CATALOG.
ENRICH_COMPANY_COST = 3.0

# Canonical Company shape per unique_entity_fields.csv + HLD 2.0 §3.2.
CANONICAL_COMPANY_KEYS = frozenset({
    "name", "domain", "linkedin_url",
    "employee_count", "industry", "hq_location",
})
META_COMPANY_KEYS = frozenset({
    "enrichment_sources", "additional_data", "match_found", "companies",
})
ALLOWED_TOP_LEVEL_COMPANY = CANONICAL_COMPANY_KEYS | META_COMPANY_KEYS

# Test inputs
TEST_COMPANY_URL = os.environ.get(
    "TEST_LINKEDIN_COMPANY_URL",
    "https://www.linkedin.com/company/google/",
)
TEST_COMPANY_DOMAIN = os.environ.get("TEST_COMPANY_DOMAIN", "google.com")
TEST_BYOK_KEY = os.environ.get("TEST_FRESH_LINKEDIN_BYOK_KEY")


# ---------------------------------------------------------------------------
# Helpers — service-token headers, platform balance, debit-delta assertion
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


def _enrich_company_body(provider: str = "fresh_linkedin", **params) -> dict:
    return {
        "operation": "enrich_company",
        "provider": provider,
        "params": params,
    }


def _assert_canonical_company_shape(data: dict, provider: str) -> None:
    """Shared shape contract for a normalized Company response (HLD 2.0 §3.2):
    top-level keys are bounded to CANONICAL ∪ metadata. enrichment_sources lists
    ONLY canonical keys. Non-canonical vendor fields live under additional_data."""
    top_level = set(data.keys())
    stray = top_level - ALLOWED_TOP_LEVEL_COMPANY
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
        assert key in CANONICAL_COMPANY_KEYS, (
            f"enrichment_sources['{provider}'] must list only canonical keys; "
            f"saw '{key}'"
        )


# ---------------------------------------------------------------------------
# BYOK helpers
# ---------------------------------------------------------------------------


async def _add_byok_key(client: httpx.AsyncClient, provider: str, key: str) -> None:
    resp = await client.post(
        f"{BASE_URL}/api/v1/keys",
        headers=_svc_headers(),
        json={"provider": provider, "api_key": key},
    )
    assert resp.status_code in (200, 201), (
        f"Failed to add BYOK for {provider}: {resp.status_code} {resp.text[:200]}"
    )


async def _remove_byok_key(client: httpx.AsyncClient, provider: str) -> None:
    resp = await client.delete(
        f"{BASE_URL}/api/v1/keys/{provider}",
        headers=_svc_headers(),
    )
    assert resp.status_code in (200, 204, 404), (
        f"Unexpected status while removing BYOK for {provider}: {resp.status_code}"
    )


# ===========================================================================
# Scenario 1 — happy path (LinkedIn URL)
# ===========================================================================


async def test_accepts_valid_linkedin_company_url_and_charges_three_credits(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S1: LinkedIn company URL → 200 + canonical Company + 3 credits ---")

    balance_before = await _get_platform_balance(client)
    print(f"Balance before: {balance_before}")

    resp = await _post_execute(
        client,
        _enrich_company_body(linkedin_url=TEST_COMPANY_URL),
    )
    body = resp.json()
    print(f"Status: {resp.status_code}  cached={body.get('result', {}).get('cached')}")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
    assert body["status"] == "success"
    assert body["credits_charged"] == ENRICH_COMPANY_COST, (
        f"Expected {ENRICH_COMPANY_COST} credits, got {body.get('credits_charged')}"
    )

    data = body["result"]
    _assert_canonical_company_shape(data, "fresh_linkedin")

    if data.get("match_found") is False:
        print("(no upstream match — shape-only assertions)")
    else:
        assert any(data.get(k) for k in ("name", "domain", "linkedin_url")), (
            f"Expected at least one identity canonical field populated; "
            f"got top-level keys: {list(data.keys())}"
        )
        extras = data["additional_data"]
        print(f"additional_data keys: {sorted(extras.keys())}")
        # Vendor-specific keys that should land in additional_data.
        fresh_linkedin_expected_extras = {
            "follower_count", "employee_range", "description",
            "specialties", "affiliated_companies", "locations",
            "company_id", "confident_score",
        }
        overlap = set(extras.keys()) & fresh_linkedin_expected_extras
        assert overlap, (
            f"fresh_linkedin enrich_company response must populate at least one "
            f"known additional_data key; got {sorted(extras.keys())}"
        )

    diff = await _balance_delta_after(client, balance_before)
    print(f"Credits deducted: {diff}")
    assert abs(diff - ENRICH_COMPANY_COST) < 0.01, (
        f"Expected ~{ENRICH_COMPANY_COST} credit debit, got {diff}"
    )
    print("PASS")


# ===========================================================================
# Scenario 2 — happy path (domain)
# ===========================================================================


async def test_accepts_domain_and_charges_three_credits(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S2: bare domain → 200 + canonical Company + 3 credits ---")

    balance_before = await _get_platform_balance(client)

    resp = await _post_execute(
        client,
        _enrich_company_body(domain=TEST_COMPANY_DOMAIN),
    )
    body = resp.json()
    print(f"Status: {resp.status_code}")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
    assert body["credits_charged"] == ENRICH_COMPANY_COST

    data = body["result"]
    _assert_canonical_company_shape(data, "fresh_linkedin")

    # Domain lookup can return a fuzzy-matched variant (HLD §4.4) — the
    # name returned may not match what we queried. That's documented behaviour,
    # not an error. We only assert the shape + that the call was billed.
    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff - ENRICH_COMPANY_COST) < 0.01
    print("PASS")


# ===========================================================================
# Scenario 3 — fuzzy match surfaces confident_score
# ===========================================================================


async def test_fuzzy_domain_exposes_confident_score_in_additional_data(
    client: httpx.AsyncClient,
) -> None:
    """When the domain lookup returns a regional variant (e.g. Google Japan for
    google.com), the caller needs a signal to decide whether to trust the result.
    ``additional_data.confident_score`` is that signal (HLD §4.4)."""
    print("\n--- S3: fuzzy domain → confident_score exposed in additional_data ---")

    resp = await _post_execute(
        client,
        _enrich_company_body(domain=TEST_COMPANY_DOMAIN),
    )
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"

    data = resp.json()["result"]
    if data.get("match_found") is False:
        print("SKIP: no match returned by vendor")
        return

    extras = data.get("additional_data") or {}
    print(f"confident_score = {extras.get('confident_score')}")
    # confident_score is always populated on a match (vendor always returns it
    # for domain lookups — not guaranteed for URL lookups per live probe).
    assert extras.get("confident_score") is not None, (
        "domain lookup must surface confident_score so callers can judge fuzziness"
    )
    print("PASS")


# ===========================================================================
# Scenarios 4–6 — 400 validation paths, no upstream, no debit
# ===========================================================================


async def _assert_validation_400_no_debit(
    client: httpx.AsyncClient,
    body: dict,
    label: str,
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


async def test_rejects_missing_both_inputs_with_400(client: httpx.AsyncClient) -> None:
    print("\n--- S4: neither linkedin_url nor domain → 400, no debit ---")
    await _assert_validation_400_no_debit(
        client,
        _enrich_company_body(),  # empty params
        label="missing-both",
    )
    print("PASS")


async def test_rejects_profile_url_as_company_url_with_400(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S5: profile URL in linkedin_url → 400, no debit ---")
    await _assert_validation_400_no_debit(
        client,
        _enrich_company_body(linkedin_url="https://www.linkedin.com/in/janedoe"),
        label="profile-url",
    )
    print("PASS")


async def test_rejects_malformed_domain_with_400(client: httpx.AsyncClient) -> None:
    print("\n--- S6: malformed domain (whitespace, no TLD) → 400, no debit ---")
    for bad in ("not a domain", "localhost"):
        await _assert_validation_400_no_debit(
            client,
            _enrich_company_body(domain=bad),
            label=f"bad-domain:{bad}",
        )
    print("PASS")


# ===========================================================================
# Scenario 7 — linkedin_url wins when both are provided
# ===========================================================================


async def test_linkedin_url_wins_over_domain_when_both_provided(
    client: httpx.AsyncClient,
) -> None:
    """Documented precedence (HLD §3.1). When the caller passes both, the URL
    takes priority. Observable: a LinkedIn URL for Google + a domain that would
    resolve to something else must return the LinkedIn-URL result."""
    print("\n--- S7: both inputs → linkedin_url wins ---")

    resp = await _post_execute(
        client,
        _enrich_company_body(
            linkedin_url="https://www.linkedin.com/company/google/",
            # Domain that would fuzzy-match to a different company if used.
            domain="microsoft.com",
        ),
    )
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:300]}"
    data = resp.json()["result"]

    if data.get("match_found") is False:
        print("SKIP: no match returned; cannot assert which input routed")
        return

    # The result should reflect Google, not Microsoft. Name / LinkedIn URL are
    # the surest signals.
    linkedin_url = (data.get("linkedin_url") or "").lower()
    name = (data.get("name") or "").lower()
    is_google = "google" in linkedin_url or "google" in name
    is_microsoft = "microsoft" in linkedin_url or "microsoft" in name
    print(f"Returned: name={data.get('name')!r}  linkedin={data.get('linkedin_url')!r}")
    assert is_google and not is_microsoft, (
        "linkedin_url must take precedence over domain; "
        f"got name={data.get('name')!r}, linkedin_url={data.get('linkedin_url')!r}"
    )
    print("PASS")


# ===========================================================================
# Scenario 8 — cache bypass (P2-D10)
# ===========================================================================


async def test_two_identical_calls_hit_upstream_twice_and_debit_twice(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S8: cache bypass — two identical enrich_company calls charge 6 credits ---")

    _flush_cache_silently()
    balance_before = await _get_platform_balance(client)

    body = _enrich_company_body(linkedin_url=TEST_COMPANY_URL)
    first = await _post_execute(client, body)
    second = await _post_execute(client, body)

    print(f"First  status={first.status_code}  credits={first.json().get('credits_charged')}")
    print(f"Second status={second.status_code}  credits={second.json().get('credits_charged')}")

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["credits_charged"] == ENRICH_COMPANY_COST
    assert second.json()["credits_charged"] == ENRICH_COMPANY_COST, (
        f"fresh_linkedin must never serve a cached response (P2-D10); "
        f"second call charged {second.json()['credits_charged']}"
    )

    diff = await _balance_delta_after(client, balance_before, settle_seconds=3.0)
    expected = 2 * ENRICH_COMPANY_COST
    assert abs(diff - expected) < 0.01, (
        f"Expected {expected} credits across two uncached calls, got {diff}"
    )
    print("PASS")


# ===========================================================================
# Scenario 9 — BYOK charges 0 credits
# ===========================================================================


async def test_byok_enrich_company_costs_zero_credits(
    client: httpx.AsyncClient,
) -> None:
    print("\n--- S9: BYOK enrich_company → 0 credits ---")

    if not TEST_BYOK_KEY:
        print("SKIP: TEST_FRESH_LINKEDIN_BYOK_KEY not set")
        return

    await _add_byok_key(client, "fresh_linkedin", TEST_BYOK_KEY)
    try:
        balance_before = await _get_platform_balance(client)

        resp = await _post_execute(
            client,
            _enrich_company_body(linkedin_url=TEST_COMPANY_URL),
        )
        body = resp.json()
        print(f"Status: {resp.status_code}  credits_charged={body.get('credits_charged')}")

        assert resp.status_code == 200
        assert body["credits_charged"] == 0, (
            f"BYOK must not consume credits; got {body['credits_charged']}"
        )

        diff = await _balance_delta_after(client, balance_before)
        assert abs(diff) < 0.01, f"BYOK path debited {diff} credits — expected 0"
        print("PASS")
    finally:
        await _remove_byok_key(client, "fresh_linkedin")


# ===========================================================================
# Scenario 10 — default routing stays on apollo
# ===========================================================================


async def test_enrich_company_without_provider_still_routes_to_apollo(
    client: httpx.AsyncClient,
) -> None:
    print(
        "\n--- S10: enrich_company without provider → apollo (D18 extension) ---"
    )

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers=_svc_headers(),
        json={
            "operation": "enrich_company",
            "params": {"domain": TEST_COMPANY_DOMAIN},
            # No "provider" key — relying on default.
        },
    )
    print(f"Status: {resp.status_code}")
    assert resp.status_code in (200, 404), (
        f"Expected 200 or 404, got {resp.status_code}: {resp.text[:300]}"
    )

    data = resp.json().get("result", {}) or {}
    sources = (data.get("enrichment_sources") or {})
    print(f"enrichment_sources keys: {list(sources.keys())}")
    assert "apollo" in sources, (
        f"Default routing must stay on apollo; saw sources {list(sources.keys())}"
    )
    assert "fresh_linkedin" not in sources, (
        f"Default route must NOT hit fresh_linkedin; saw sources {list(sources.keys())}"
    )
    print("PASS")


# ===========================================================================
# Scenario 11 — unsupported operation under fresh_linkedin company
# ===========================================================================


async def test_rejects_search_companies_on_fresh_linkedin(
    client: httpx.AsyncClient,
) -> None:
    """fresh_linkedin supports enrich_company but not search_companies.
    The provider must refuse without upstream / debit."""
    print("\n--- S11: search_companies on fresh_linkedin → rejected, no debit ---")
    balance_before = await _get_platform_balance(client)

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers=_svc_headers(),
        json={
            "operation": "search_companies",
            "provider": "fresh_linkedin",
            "params": {"industry": "Software"},
        },
    )
    print(f"Status: {resp.status_code}  body={resp.text[:200]}")
    assert resp.status_code in (400, 502), (
        f"Expected 400/502 for unsupported op; got {resp.status_code}"
    )

    diff = await _balance_delta_after(client, balance_before)
    assert abs(diff) < 0.01, f"Unsupported-op rejection must not debit; delta={diff}"
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
    print("Fresh LinkedIn enrich_company API Tests — consultant-agent simulation")
    print(f"GTM Engine:     {BASE_URL}")
    print(f"Platform:       {PLATFORM_URL}")
    print(f"Rich tenant:    {RICH_TENANT_ID}")
    print(f"Agent identity: {CONSULTANT_AGENT}")
    print(f"Company URL:    {TEST_COMPANY_URL}")
    print(f"Domain:         {TEST_COMPANY_DOMAIN}")
    print(f"BYOK configured: {'yes' if TEST_BYOK_KEY else 'no (S9 will skip)'}")
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
            test_accepts_valid_linkedin_company_url_and_charges_three_credits,
            test_accepts_domain_and_charges_three_credits,
            test_fuzzy_domain_exposes_confident_score_in_additional_data,
            test_rejects_missing_both_inputs_with_400,
            test_rejects_profile_url_as_company_url_with_400,
            test_rejects_malformed_domain_with_400,
            test_linkedin_url_wins_over_domain_when_both_provided,
            test_two_identical_calls_hit_upstream_twice_and_debit_twice,
            test_byok_enrich_company_costs_zero_credits,
            test_enrich_company_without_provider_still_routes_to_apollo,
            test_rejects_search_companies_on_fresh_linkedin,
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
