"""API tests for pseudo-tenant sync endpoints (POST/PATCH /api/v1/tenants).

Tests run against a live server. Requires:
    1. GTM_ENGINE_SERVICE_TOKEN set in .env
    2. Server running: cd server && uvicorn server.app:app --reload
    3. Run: python test_tenant_sync_api.py
"""

from __future__ import annotations

import asyncio
import time

import httpx

BASE_URL = "http://localhost:8000"
SERVICE_TOKEN = "XRWnB_IpZa0f3T1G1rpsItpa_S2qJKHBZuY_3Bc8WDM"
TENANTS_URL = f"{BASE_URL}/api/v1/tenants"

# Use a unique tenant ID per run to avoid collisions
TEST_TENANT_ID = str(int(time.time()))


def _svc_headers() -> dict[str, str]:
    """Headers with valid service token."""
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SERVICE_TOKEN}",
    }


def _jwt_headers() -> dict[str, str]:
    """Headers with a fake JWT (not a service token)."""
    return {
        "Content-Type": "application/json",
        "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.fake",
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


async def test_create_tenant(client: httpx.AsyncClient) -> None:
    """POST /api/v1/tenants with service token -> 201, tenant in DB."""
    print(f"\n--- Test 1: Create tenant (id={TEST_TENANT_ID}) ---")
    resp = await client.post(
        TENANTS_URL,
        headers=_svc_headers(),
        json={"id": TEST_TENANT_ID, "name": "Acme", "domain": "acme.com"},
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}"
    body = resp.json()
    assert body["id"] == TEST_TENANT_ID
    assert body["name"] == "Acme"
    assert body["domain"] == "acme.com"
    print("PASS")


async def test_idempotent_create(client: httpx.AsyncClient) -> None:
    """POST same tenant again -> 200, returns existing unchanged."""
    print(f"\n--- Test 2: Idempotent create (id={TEST_TENANT_ID}) ---")
    resp = await client.post(
        TENANTS_URL,
        headers=_svc_headers(),
        json={"id": TEST_TENANT_ID, "name": "Acme", "domain": "acme.com"},
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    body = resp.json()
    assert body["id"] == TEST_TENANT_ID
    assert body["name"] == "Acme"
    print("PASS")


async def test_update_tenant(client: httpx.AsyncClient) -> None:
    """PATCH /api/v1/tenants/{id} with name -> 200, name updated, domain preserved."""
    print(f"\n--- Test 3: Update tenant name (id={TEST_TENANT_ID}) ---")
    resp = await client.patch(
        f"{TENANTS_URL}/{TEST_TENANT_ID}",
        headers=_svc_headers(),
        json={"name": "Acme Corp"},
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    body = resp.json()
    assert body["name"] == "Acme Corp"
    assert body["domain"] == "acme.com", "Domain should be preserved"
    print("PASS")


async def test_patch_nonexistent(client: httpx.AsyncClient) -> None:
    """PATCH /api/v1/tenants/999999 -> 404."""
    print("\n--- Test 4: PATCH nonexistent tenant ---")
    resp = await client.patch(
        f"{TENANTS_URL}/999999",
        headers=_svc_headers(),
        json={"name": "Ghost"},
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
    print("PASS")


async def test_reject_jwt_auth_post(client: httpx.AsyncClient) -> None:
    """POST with JWT instead of service token -> 403."""
    print("\n--- Test 5: Reject JWT on POST ---")
    resp = await client.post(
        TENANTS_URL,
        headers=_jwt_headers(),
        json={"id": "999", "name": "Bad", "domain": "bad.com"},
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
    print("PASS")


async def test_reject_jwt_auth_patch(client: httpx.AsyncClient) -> None:
    """PATCH with JWT instead of service token -> 403."""
    print("\n--- Test 6: Reject JWT on PATCH ---")
    resp = await client.patch(
        f"{TENANTS_URL}/{TEST_TENANT_ID}",
        headers=_jwt_headers(),
        json={"name": "Hacked"},
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
    print("PASS")


async def test_reject_non_numeric_id(client: httpx.AsyncClient) -> None:
    """POST with non-numeric id -> 422 (validation error)."""
    print("\n--- Test 7: Reject non-numeric ID ---")
    resp = await client.post(
        TENANTS_URL,
        headers=_svc_headers(),
        json={"id": "tn_abc", "name": "Acme", "domain": "acme.com"},
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"
    print("PASS")


async def test_reject_empty_name(client: httpx.AsyncClient) -> None:
    """POST with empty name -> 422."""
    print("\n--- Test 8: Reject empty name ---")
    resp = await client.post(
        TENANTS_URL,
        headers=_svc_headers(),
        json={"id": "1", "name": "", "domain": "x.com"},
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"
    print("PASS")


async def test_reject_empty_patch_body(client: httpx.AsyncClient) -> None:
    """PATCH with {} -> 422 (at least one field required)."""
    print("\n--- Test 9: Reject empty PATCH body ---")
    resp = await client.patch(
        f"{TENANTS_URL}/{TEST_TENANT_ID}",
        headers=_svc_headers(),
        json={},
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"
    print("PASS")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def main():
    print("=" * 60)
    print("Tenant Sync API Tests")
    print(f"Server:    {BASE_URL}")
    print(f"Token:     {SERVICE_TOKEN[:8]}...")
    print(f"Tenant ID: {TEST_TENANT_ID}")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            health = await client.get(f"{BASE_URL}/health")
            print(f"\nServer health: {health.status_code}")
        except httpx.ConnectError:
            print("\nERROR: Cannot connect to server. Start it with:")
            print("  cd server && uvicorn server.app:app --reload")
            return

        passed = 0
        failed = 0
        tests = [
            test_create_tenant,
            test_idempotent_create,
            test_update_tenant,
            test_patch_nonexistent,
            test_reject_jwt_auth_post,
            test_reject_jwt_auth_patch,
            test_reject_non_numeric_id,
            test_reject_empty_name,
            test_reject_empty_patch_body,
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
