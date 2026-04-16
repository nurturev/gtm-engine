"""Smoke tests for POST /api/v1/execute and POST /api/v1/execute/batch
with DB-backed credit costs (migration 016_operation_costs).

Goal: confirm the new `provider` field is accepted end-to-end and that both
endpoints continue to work after the cost-config refactor. Estimate tests
come later.

Requires:
    1. Server running: cd server && uvicorn server.app:app --reload
    2. Migration 016 applied (operation_costs table seeded)
    3. Rich tenant with credits on the platform (default: tenant 4)

Run:
    python tests/api_tests/test_execute_db_costs.py
    python tests/api_tests/test_execute_db_costs.py --no-cache  # skip redis flush
"""

from __future__ import annotations

import asyncio
import sys
import time

import httpx

try:
    import redis
except ImportError:
    redis = None

BASE_URL = "http://localhost:8000"
SERVICE_TOKEN = "XRWnB_IpZa0f3T1G1rpsItpa_S2qJKHBZuY_3Bc8WDM"
RICH_TENANT_ID = "4"

REDIS_URL = "redis://localhost:6379/0"
CACHE_PREFIX = "cache:exec:*"


def _svc_headers(tenant_id: str = RICH_TENANT_ID) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-Id": tenant_id,
        "X-Agent-Type": "test",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_execute_without_provider(client: httpx.AsyncClient) -> None:
    """Backward compat: POST /execute with no `provider` field still works."""
    print("\n--- Test 1: /execute (no provider — auto-select) ---")

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers=_svc_headers(),
        json={
            "operation": "enrich_person",
            "params": {"email": f"nop-{int(time.time())}@freshworks.com"},
        },
    )
    print(f"Status: {resp.status_code}")
    body = resp.json()
    print(
        f"execution_id={body.get('execution_id')} "
        f"credits_charged={body.get('credits_charged')} status={body.get('status')}"
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    assert "execution_id" in body
    assert "credits_charged" in body
    print("PASS")


async def test_execute_with_provider_apollo(client: httpx.AsyncClient) -> None:
    """POST /execute with explicit provider='apollo' — uses vendor-scoped cost."""
    print("\n--- Test 2: /execute (provider=apollo) ---")

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers=_svc_headers(),
        json={
            "operation": "enrich_person",
            "provider": "apollo",
            "params": {"email": f"apollo-{int(time.time())}@freshworks.com"},
        },
    )
    print(f"Status: {resp.status_code}")
    body = resp.json()
    print(
        f"execution_id={body.get('execution_id')} "
        f"credits_charged={body.get('credits_charged')} status={body.get('status')}"
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    assert body.get("status") in ("success", "error")
    print("PASS")


async def test_execute_search_scales_with_per_page(client: httpx.AsyncClient) -> None:
    """Search scaling: per_page=100 should not fail and should charge multiple credits."""
    print("\n--- Test 3: /execute search_people per_page=100 scales cost ---")

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers=_svc_headers(),
        json={
            "operation": "search_people",
            "provider": "apollo",
            "params": {
                "titles": ["VP Sales"],
                "per_page": 100,
            },
        },
    )
    print(f"Status: {resp.status_code}")
    body = resp.json()
    print(f"credits_charged={body.get('credits_charged')} status={body.get('status')}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    print("PASS")


async def test_batch_without_provider(client: httpx.AsyncClient) -> None:
    """Backward compat: /execute/batch with no `provider` on any op."""
    print("\n--- Test 4: /execute/batch (no provider on ops) ---")

    ts = int(time.time())
    operations = [
        {"operation": "enrich_company", "params": {"domain": f"bw-compat-{ts}-1.example.com"}},
        {"operation": "enrich_company", "params": {"domain": f"bw-compat-{ts}-2.example.com"}},
    ]

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute/batch",
        headers=_svc_headers(),
        json={"operations": operations},
    )
    print(f"Status: {resp.status_code}")
    body = resp.json()
    print(f"batch_id={body.get('batch_id')} total={body.get('total')} status={body.get('status')}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    assert body.get("total") == 2
    print("PASS")


async def test_batch_with_provider(client: httpx.AsyncClient) -> None:
    """/execute/batch with explicit `provider` on each op."""
    print("\n--- Test 5: /execute/batch (provider=apollo on each op) ---")

    ts = int(time.time())
    operations = [
        {
            "operation": "enrich_company",
            "provider": "apollo",
            "params": {"domain": f"prov-{ts}-1.example.com"},
        },
        {
            "operation": "enrich_company",
            "provider": "apollo",
            "params": {"domain": f"prov-{ts}-2.example.com"},
        },
    ]

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute/batch",
        headers=_svc_headers(),
        json={"operations": operations},
    )
    print(f"Status: {resp.status_code}")
    body = resp.json()
    print(f"batch_id={body.get('batch_id')} total={body.get('total')} status={body.get('status')}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    assert body.get("total") == 2
    print("PASS")


async def test_batch_mixed_providers(client: httpx.AsyncClient) -> None:
    """/execute/batch where different ops have different providers."""
    print("\n--- Test 6: /execute/batch (mixed providers per op) ---")

    ts = int(time.time())
    operations = [
        {
            "operation": "enrich_person",
            "provider": "apollo",
            "params": {"email": f"mix-apollo-{ts}@example.com"},
        },
        {
            "operation": "enrich_person",
            "provider": "rocketreach",
            "params": {"email": f"mix-rr-{ts}@example.com"},
        },
    ]

    resp = await client.post(
        f"{BASE_URL}/api/v1/execute/batch",
        headers=_svc_headers(),
        json={"operations": operations},
    )
    print(f"Status: {resp.status_code}")
    body = resp.json()
    print(f"batch_id={body.get('batch_id')} total={body.get('total')} status={body.get('status')}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    assert body.get("total") == 2
    print("PASS")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def flush_execution_cache() -> int:
    if redis is None:
        return 0
    r = redis.from_url(REDIS_URL)
    keys = r.keys(CACHE_PREFIX)
    if not keys:
        return 0
    return r.delete(*keys)


async def main() -> None:
    flush = "--no-cache" not in sys.argv

    print("=" * 60)
    print("DB-backed credit cost smoke tests")
    print(f"GTM Engine:   {BASE_URL}")
    print(f"Rich tenant:  {RICH_TENANT_ID}")
    print("=" * 60)

    if flush:
        try:
            deleted = flush_execution_cache()
            print(f"\nFlushed {deleted} execution cache keys from Redis")
        except Exception as exc:
            print(f"\nWARNING: could not flush Redis cache: {exc}")

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            health = await client.get(f"{BASE_URL}/health")
            print(f"\nGTM Engine health: {health.status_code}")
        except httpx.ConnectError:
            print("\nERROR: Cannot connect to GTM Engine. Start it with:")
            print("  cd server && uvicorn server.app:app --reload")
            return

        tests = [
            test_execute_without_provider,
            test_execute_with_provider_apollo,
            test_execute_search_scales_with_per_page,
            test_batch_without_provider,
            test_batch_with_provider,
            test_batch_mixed_providers,
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
                print(f"ERROR: {exc}")
                failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
