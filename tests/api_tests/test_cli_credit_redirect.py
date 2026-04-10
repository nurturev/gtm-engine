"""API tests for CLI credit commands redirect (Task 6).

Tests that CLI credit commands correctly route through the platform
credit service instead of the local DB.

- balance: returns platform balance via /credits/balance endpoint
- balance via tenant_id (service token): server calls /private/tenant/credits
- topup: CLI opens https://app.nrev.ai/payments (tested via CLI subprocess)
- history: CLI prints redirect message (tested via CLI subprocess)

Uses tenant 4 for balance tests.

Requires:
    1. PLATFORM_CREDIT_SERVICE_URL and PLATFORM_CREDIT_SERVICE_TOKEN in .env
    2. Server running: cd server && uvicorn server.app:app --reload
    3. Run: python test_cli_credit_redirect.py
"""

from __future__ import annotations

import asyncio
import subprocess
import sys

import httpx

BASE_URL = "http://localhost:8000"
SERVICE_TOKEN = "XRWnB_IpZa0f3T1G1rpsItpa_S2qJKHBZuY_3Bc8WDM"
TENANT_ID = "4"

# UM platform
PLATFORM_URL = "https://umws.public.staging.nurturev.com/private"
PLATFORM_TOKEN = "Na3G8LOC84N8J8y32A5mJUwP7Avb0P57"


def _svc_headers(tenant_id: str = TENANT_ID) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-Id": tenant_id,
        "X-Agent-Type": "test",
    }


def _platform_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {PLATFORM_TOKEN}"}


async def _get_platform_balance(client: httpx.AsyncClient) -> float:
    resp = await client.get(
        f"{PLATFORM_URL}/tenant/credits",
        params={"tenant_id": int(TENANT_ID)},
        headers=_platform_headers(),
    )
    assert resp.status_code == 200, (
        f"Direct UM balance check failed: status={resp.status_code} body={resp.text[:200]}"
    )
    return float(resp.json())


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


async def test_balance_via_service_token(client: httpx.AsyncClient) -> None:
    """Test 1: GET /credits/balance with service token → platform balance.

    Service token path calls check_platform_credits(tenant_id) since
    there's no user_id to use.
    """
    print("\n--- Test 1: Balance via service token → platform balance ---")

    platform_balance = await _get_platform_balance(client)
    print(f"Platform balance (direct): {platform_balance}")

    resp = await client.get(
        f"{BASE_URL}/api/v1/credits/balance",
        headers=_svc_headers(),
    )
    print(f"Status: {resp.status_code}")
    body = resp.json()
    print(f"Body:   {body}")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert body["balance"] == platform_balance, (
        f"Balance mismatch: GTM={body['balance']}, Platform={platform_balance}"
    )
    print("PASS (service token → tenant_id-based balance)")


async def test_cli_credits_balance() -> None:
    """Test 3: nrev-lite credits balance — returns platform balance.

    Runs the CLI command as a subprocess and checks the output.
    """
    print("\n--- Test 3: CLI 'nrev-lite credits balance' ---")

    result = subprocess.run(
        ["nrev-lite", "credits", "balance"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    print(f"Exit code: {result.returncode}")
    print(f"Output: {result.stdout.strip()}")
    if result.stderr:
        print(f"Stderr: {result.stderr.strip()}")

    if result.returncode != 0:
        output = result.stdout + result.stderr
        if "Not logged in" in output or "Session expired" in output:
            print("SKIP — CLI not authenticated (run: nrev-lite auth login)")
            return
        assert False, f"CLI exited with code {result.returncode}: {output.strip()}"

    # Output should contain a balance number
    output = result.stdout + result.stderr
    assert any(c.isdigit() for c in output), (
        f"Expected balance output with digits, got: {output}"
    )
    print("PASS")


async def test_cli_credits_topup() -> None:
    """Test 4: nrev-lite credits topup — prints https://app.nrev.ai/payments.

    We can't test browser opening, but we verify the URL is in the output.
    """
    print("\n--- Test 4: CLI 'nrev-lite credits topup' ---")

    result = subprocess.run(
        ["nrev-lite", "credits", "topup"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    print(f"Exit code: {result.returncode}")
    output = result.stdout + result.stderr
    print(f"Output: {output.strip()}")

    if "Not logged in" in output or "Session expired" in output:
        print("SKIP — CLI not authenticated")
        return

    assert "app.nrev.ai/payments" in output, (
        f"Expected 'app.nrev.ai/payments' in output, got: {output}"
    )
    print("PASS")


async def test_cli_credits_history() -> None:
    """Test 5: nrev-lite credits history — prints redirect message.

    Should print the redirect URL without making an API call.
    """
    print("\n--- Test 5: CLI 'nrev-lite credits history' ---")

    result = subprocess.run(
        ["nrev-lite", "credits", "history"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    print(f"Exit code: {result.returncode}")
    output = result.stdout + result.stderr
    print(f"Output: {output.strip()}")

    if "Not logged in" in output or "Session expired" in output:
        print("SKIP — CLI not authenticated")
        return

    assert "app.nrev.ai/usage" in output, (
        f"Expected 'app.nrev.ai/usage' in output, got: {output}"
    )
    print("PASS")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def main():
    print("=" * 60)
    print("CLI Credit Commands Redirect Tests (Task 6)")
    print(f"GTM Engine:  {BASE_URL}")
    print(f"Platform:    {PLATFORM_URL}")
    print(f"Tenant ID:   {TENANT_ID}")
    print("=" * 60)

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
            platform_balance = await _get_platform_balance(client)
            print(f"Platform balance for tenant {TENANT_ID}: {platform_balance}")
        except Exception as e:
            print(f"\nERROR: Cannot reach UM platform at {PLATFORM_URL}: {e}")
            print("Fix the PLATFORM_URL or check connectivity before running tests.")
            return

        passed = 0
        failed = 0

        # API-level tests
        api_tests = [
            test_balance_via_service_token,
        ]
        for test in api_tests:
            try:
                await test(client)
                passed += 1
            except AssertionError as e:
                print(f"FAIL: {e}")
                failed += 1
            except Exception as e:
                print(f"ERROR: {e}")
                failed += 1

    # CLI-level tests (no httpx client needed)
    cli_tests = [
        test_cli_credits_balance,
        test_cli_credits_topup,
        test_cli_credits_history,
    ]
    for test in cli_tests:
        try:
            await test()
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
