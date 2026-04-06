"""Test service token authentication against gtm-engine.

Simulates the workflow_studio GTMEngineClient making calls
to execution endpoints with the new service token auth mechanism.

Usage:
    # 1. Add GTM_ENGINE_SERVICE_TOKEN to your .env file
    # 2. Start the server: cd server && uvicorn server.app:app --reload
    # 3. Run: python test_service_token_auth.py
"""

import asyncio
import httpx

BASE_URL = "http://localhost:8000"
SERVICE_TOKEN = "XRWnB_IpZa0f3T1G1rpsItpa_S2qJKHBZuY_3Bc8WDM"
TENANT_ID = "test-tenant"
THREAD_ID = "test-thread-001"


def _headers(
    token: str | None = SERVICE_TOKEN,
    tenant_id: str | None = TENANT_ID,
    thread_id: str | None = THREAD_ID,
) -> dict[str, str]:
    h: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if tenant_id:
        h["X-Tenant-Id"] = tenant_id
    if thread_id:
        h["X-Thread-Id"] = thread_id
        h["X-Workflow-Id"] = thread_id
    h["X-Agent-Type"] = "consultant"
    return h


async def test_service_token_execute(client: httpx.AsyncClient) -> None:
    """Test 1: Service token auth on /execute — should succeed (or 402 if no credits)."""
    print("\n--- Test 1: Service token auth on POST /execute ---")
    resp = await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers=_headers(),
        json={"operation": "enrich_company", "params": {"domain": "sully.ai"}},
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    # 200 = success, 402 = no credits (auth worked, billing blocked it)
    assert resp.status_code in (200, 402), f"Expected 200 or 402, got {resp.status_code}"
    print("PASS (auth succeeded)")


async def test_missing_tenant_id(client: httpx.AsyncClient) -> None:
    """Test 2: Service token without X-Tenant-Id — should return 400."""
    print("\n--- Test 2: Missing X-Tenant-Id header ---")
    resp = await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers=_headers(tenant_id=None),
        json={"operation": "enrich_company", "params": {"domain": "sully.ai"}},
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
    print("PASS")


async def test_wrong_token(client: httpx.AsyncClient) -> None:
    """Test 3: Wrong token — should fall through to JWT decode and return 401."""
    print("\n--- Test 3: Wrong token (not service token, not valid JWT) ---")
    resp = await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers=_headers(token="wrong-token-value"),
        json={"operation": "enrich_company", "params": {"domain": "sully.ai"}},
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
    print("PASS")


async def test_no_auth_header(client: httpx.AsyncClient) -> None:
    """Test 4: No Authorization header — should return 422 (missing required header)."""
    print("\n--- Test 4: No Authorization header ---")
    resp = await client.post(
        f"{BASE_URL}/api/v1/execute",
        headers={"Content-Type": "application/json"},
        json={"operation": "enrich_company", "params": {"domain": "sully.ai"}},
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"
    print("PASS")


async def test_credits_balance(client: httpx.AsyncClient) -> None:
    """Test 5: Service token auth on /credits/balance — lightweight endpoint."""
    print("\n--- Test 5: GET /credits/balance with service token ---")
    resp = await client.get(
        f"{BASE_URL}/api/v1/credits/balance",
        headers=_headers(),
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    print("PASS")


async def test_search_patterns(client: httpx.AsyncClient) -> None:
    """Test 6: Service token auth on /search/patterns — should succeed."""
    print("\n--- Test 6: GET /search/patterns with service token ---")
    resp = await client.get(
        f"{BASE_URL}/api/v1/search/patterns",
        headers=_headers(),
        params={"platform": "linkedin_jobs"},
    )
    print(f"Status: {resp.status_code}")
    # Don't print full body — it's large
    body = resp.json()
    print(f"Body:   {list(body.keys()) if isinstance(body, dict) else body}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    print("PASS")


async def test_cost_estimate(client: httpx.AsyncClient) -> None:
    """Test 7: Service token auth on /execute/cost — should succeed."""
    print("\n--- Test 7: POST /execute/cost with service token ---")
    resp = await client.post(
        f"{BASE_URL}/api/v1/execute/cost",
        headers=_headers(),
        json={"operation": "enrich_company", "params": {"domain": "sully.ai"}},
    )
    print(f"Status: {resp.status_code}")
    print(f"Body:   {resp.json()}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    print("PASS")


async def main():
    print("=" * 60)
    print("Service Token Auth Tests")
    print(f"Server:  {BASE_URL}")
    print(f"Token:   {SERVICE_TOKEN[:8]}...")
    print(f"Tenant:  {TENANT_ID}")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Check server is up
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
            test_service_token_execute,
            test_missing_tenant_id,
            test_wrong_token,
            test_no_auth_header,
            test_credits_balance,
            test_search_patterns,
            test_cost_estimate,
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
