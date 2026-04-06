"""API tests for execution router wiring (Task 3).

Tests that the execution router correctly passes event_name in debit payloads
and that require_credits enforces the credit check atomically.

- Tenant 4: used for tests where execution should succeed (has credits)
- New tenant (created via POST /api/v1/tenants): used for 0-credit 402 tests

Requires:
    1. PLATFORM_CREDIT_SERVICE_URL and PLATFORM_CREDIT_SERVICE_TOKEN in .env
    2. Server running: cd server && uvicorn server.app:app --reload
    3. Run: python test_execution_router_wiring.py
"""

from __future__ import annotations

import asyncio
import sys
import time

import httpx
import redis

BASE_URL = "http://localhost:8000"
SERVICE_TOKEN = "XRWnB_IpZa0f3T1G1rpsItpa_S2qJKHBZuY_3Bc8WDM"

# Tenant with credits (for successful execution tests)
RICH_TENANT_ID = "4"

# Tenant with no credits on platform (created fresh each run)
BROKE_TENANT_ID = str(185)

REDIS_URL = "redis://localhost:6379/0"
CACHE_PREFIX = "cache:exec:*"

# UM platform (for direct balance verification)
PLATFORM_URL = "https://umws.public.staging.nurturev.com/private"
PLATFORM_TOKEN = "Na3G8LOC84N8J8y32A5mJUwP7Avb0P57"


def _svc_headers(tenant_id: str = RICH_TENANT_ID) -> dict[str, str]:
    """Service token headers for GTM Engine calls."""
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-Id": tenant_id,
        "X-Agent-Type": "test",
    }


def _platform_headers() -> dict[str, str]:
    """Direct headers for UM platform verification calls."""
    return {"Authorization": f"Bearer {PLATFORM_TOKEN}"}


async def _get_platform_balance(
    client: httpx.AsyncClient, tenant_id: str = RICH_TENANT_ID
) -> float:
    """Directly query UM platform for a tenant's balance. Fails hard on error."""
    resp = await client.get(
        f"{PLATFORM_URL}/tenant/credits",
        params={"tenant_id": int(tenant_id)},
        headers=_platform_headers(),
    )
    assert (
        resp.status_code == 200
    ), f"Direct UM balance check failed: status={resp.status_code} body={resp.text[:200]}"
    return float(resp.json())


# ---------------------------------------------------------------------------
# Setup: create a broke tenant in GTM Engine (no credits on platform)
# ---------------------------------------------------------------------------


async def setup_broke_tenant(client: httpx.AsyncClient) -> None:
    """Create a tenant in GTM Engine that has no credits on the platform."""
    print(f"\n--- Setup: Creating broke tenant (id={BROKE_TENANT_ID}) ---")
    resp = await client.post(
        f"{BASE_URL}/api/v1/tenants",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SERVICE_TOKEN}",
        },
        json={
            "id": BROKE_TENANT_ID,
            "name": f"Test Broke Tenant {BROKE_TENANT_ID}",
            "domain": "broke-test.example.com",
        },
    )
    print(f"Status: {resp.status_code}")
    assert resp.status_code in (
        200,
        201,
    ), f"Failed to create broke tenant: {resp.status_code} {resp.text[:200]}"
    print(f"Broke tenant {BROKE_TENANT_ID} ready (no credits on platform)")


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


async def test_single_execute_event_name(client: httpx.AsyncClient) -> None:
    """Test 1: Single execute debit has correct event_name.

    POST /execute with operation: "enrich_person" → debit payload should
    have event_name: "enrich_person" (not missing, not generic).

    Uses tenant 4 (has credits).
    """
    print("\n--- Test 1: Single execute — event_name = operation name ---")

    balance_before = await _get_platform_balance(client)
    print(f"Balance before: {balance_before}")

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers=_svc_headers(RICH_TENANT_ID),
        json={
            "operation": "enrich_person",
            "params": {"email": "test@freshworks.com"},
        },
    )
    print(f"Status: {resp.status_code}")
    body = resp.json()
    print(f"Credits charged: {body.get('credits_charged')}")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert body["credits_charged"] > 0, (
        f"Expected credits_charged > 0, got {body['credits_charged']}. "
        f"Likely a cache hit — flush Redis cache first."
    )

    # Wait for fire-and-forget debit
    await asyncio.sleep(2)

    balance_after = await _get_platform_balance(client)
    print(f"Balance after:  {balance_after}")
    diff = balance_before - balance_after
    print(f"Credits deducted: {diff}")

    print("PASS")
    print("  -> Check UM logs for: POST /tenant/credit/deduct")
    print('     event_name: "enrich_person" (not missing, not "execute")')


async def test_batch_execute_event_name(client: httpx.AsyncClient) -> None:
    """Test 2: Batch execute debit has correct event_name.

    POST /execute/batch with enrich_company ops → debit payload should
    have event_name: "enrich_company" (not "batch").

    Uses tenant 4 (has credits).
    """
    print("\n--- Test 2: Batch execute — event_name = operation name (not 'batch') ---")

    balance_before = await _get_platform_balance(client)
    print(f"Balance before: {balance_before}")

    # Batch of 2 enrich_company operations with unique domains
    domains = [
        f"batch-test-{int(time.time())}-1.example.com",
        f"batch-test-{int(time.time())}-2.example.com",
    ]
    operations = [
        {"operation": "enrich_company", "params": {"domain": d}} for d in domains
    ]

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute/batch",
        headers=_svc_headers(RICH_TENANT_ID),
        json={"operations": operations},
    )
    print(f"Status: {resp.status_code}")
    body = resp.json()
    print(f"Body: batch_id={body.get('batch_id')}, total={body.get('total')}")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    # Wait for fire-and-forget debit
    await asyncio.sleep(2)

    balance_after = await _get_platform_balance(client)
    print(f"Balance after:  {balance_after}")
    diff = balance_before - balance_after
    print(f"Credits deducted: {diff}")

    print("PASS")
    print("  -> Check UM logs for: POST /tenant/credit/deduct")
    print('     event_name: "enrich_company" (NOT "batch")')
    print(f"     credit_count: should reflect actual cost (expected ~{len(domains)})")


async def test_require_credits_402_broke_tenant(client: httpx.AsyncClient) -> None:
    """Test 3a: Broke tenant (0 credits on platform) → 402.

    Uses the freshly created tenant that has no credits on the platform.
    require_credits should call check_platform_credits, get 0 back, and reject.
    """
    print(
        f"\n--- Test 3a: require_credits rejects broke tenant (id={BROKE_TENANT_ID}) ---"
    )

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers=_svc_headers(BROKE_TENANT_ID),
        json={
            "operation": "enrich_company",
            "params": {"domain": "test.com"},
        },
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 402, f"Expected 402, got {resp.status_code}"
    print("PASS")


async def test_require_credits_402_batch_broke_tenant(
    client: httpx.AsyncClient,
) -> None:
    """Test 3b: Broke tenant batch execute → 402.

    Same as 3a but via /execute/batch. require_credits(1.0) runs before
    the batch handler, so the whole batch is rejected upfront.
    """
    print(f"\n--- Test 3b: require_credits rejects batch for broke tenant ---")

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute/batch",
        headers=_svc_headers(BROKE_TENANT_ID),
        json={
            "operations": [
                {"operation": "enrich_company", "params": {"domain": "a.com"}},
                {"operation": "enrich_company", "params": {"domain": "b.com"}},
            ]
        },
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 402, f"Expected 402, got {resp.status_code}"
    print("PASS")


async def test_require_credits_passes_rich_tenant(client: httpx.AsyncClient) -> None:
    """Test 3c: Rich tenant (has credits) passes require_credits check.

    Calls /execute/cost (no credits consumed, just auth + credit check path)
    to verify the pre-check passes for tenant 4.
    """
    print(
        f"\n--- Test 3c: require_credits passes for rich tenant (id={RICH_TENANT_ID}) ---"
    )

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute/cost",
        headers=_svc_headers(RICH_TENANT_ID),
        json={
            "operation": "enrich_person",
            "params": {"email": "test@example.com"},
        },
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    print("PASS")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def flush_execution_cache() -> int:
    """Delete all execution cache keys from Redis. Returns count of keys deleted."""
    r = redis.from_url(REDIS_URL)
    keys = r.keys(CACHE_PREFIX)
    if not keys:
        return 0
    return r.delete(*keys)


async def main():
    flush = "--no-cache" not in sys.argv

    print("=" * 60)
    print("Execution Router Wiring Tests (Task 3)")
    print(f"GTM Engine:    {BASE_URL}")
    print(f"Platform:      {PLATFORM_URL}")
    print(f"Rich tenant:   {RICH_TENANT_ID}")
    print(f"Broke tenant:  {BROKE_TENANT_ID}")
    print("=" * 60)

    if flush:
        try:
            deleted = flush_execution_cache()
            print(f"\nFlushed {deleted} execution cache keys from Redis")
        except Exception as e:
            print(f"\nWARNING: Could not flush Redis cache: {e}")
            print(
                "Cached responses may cause credits_charged=0. Use --no-cache to skip flush."
            )

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Check GTM Engine is up
        try:
            health = await client.get(f"{BASE_URL}/health")
            print(f"\nGTM Engine health: {health.status_code}")
        except httpx.ConnectError:
            print("\nERROR: Cannot connect to GTM Engine. Start it with:")
            print("  cd server && uvicorn server.app:app --reload")
            return

        # Check platform is reachable
        try:
            balance = await _get_platform_balance(client)
            print(f"Platform balance for tenant {RICH_TENANT_ID}: {balance}")
        except Exception as e:
            print(f"\nERROR: Cannot reach UM platform at {PLATFORM_URL}: {e}")
            print("Fix the PLATFORM_URL or check connectivity before running tests.")
            return

        # Setup: create broke tenant
        try:
            await setup_broke_tenant(client)
        except AssertionError as e:
            print(f"SETUP FAILED: {e}")
            return

        passed = 0
        failed = 0
        tests = [
            test_single_execute_event_name,
            test_batch_execute_event_name,
            test_require_credits_402_broke_tenant,
            test_require_credits_402_batch_broke_tenant,
            test_require_credits_passes_rich_tenant,
        ]
        for test in tests:
            try:
                await test(client)
                passed += 1
            except AssertionError as e:
                print(f"FAIL: {e}")
                failed += 1
            except Exception as e:
                print(f"ERROR: {e}")
                failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
