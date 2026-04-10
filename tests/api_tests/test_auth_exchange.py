"""API tests for /api/v1/auth/exchange and /api/v1/auth/refresh.

Tests run against a live server. Requires:
    1. SUPABASE_JWT_SECRET set in .env (matching the value used here)
    2. JWT_SECRET_KEY set in .env
    3. Server running: cd server && uvicorn server.app:app --reload
    4. Run: python tests/api_tests/test_auth_exchange.py

These tests exercise the full HTTP path: a Supabase-shaped JWT signed with
the configured SUPABASE_JWT_SECRET is sent to /auth/exchange, the response
is validated, and the returned refresh token is used against /auth/refresh.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import httpx
from jose import jwt

BASE_URL = "http://localhost:8000"
SUPABASE_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
EXCHANGE_URL = f"{BASE_URL}/api/v1/auth/exchange"
REFRESH_URL = f"{BASE_URL}/api/v1/auth/refresh"

TEST_TENANT_ID = "test-exchange-tenant"
TEST_SUPABASE_SUB = "00000000-0000-0000-0000-000000000abc"
TEST_EMAIL = "exchange-test@acme.com"


def _supabase_jwt(*, sub: str = TEST_SUPABASE_SUB, expired: bool = False) -> str:
    payload = {
        "sub": sub,
        "aud": "authenticated",
        "role": "authenticated",
        "email": TEST_EMAIL,
        "exp": int(
            (
                datetime.now(timezone.utc)
                + (timedelta(hours=-1) if expired else timedelta(hours=1))
            ).timestamp()
        ),
    }
    return jwt.encode(payload, SUPABASE_SECRET, algorithm="HS256")


async def test_exchange_happy_path(client: httpx.AsyncClient) -> str:
    """POST /auth/exchange with a valid Supabase JWT -> 200 + access + refresh."""
    print("\n--- Test 1: Exchange happy path ---")
    resp = await client.post(
        EXCHANGE_URL,
        json={
            "supabase_jwt": _supabase_jwt(),
            "tenant_id": TEST_TENANT_ID,
            "email": TEST_EMAIL,
            "channel": "cli",
        },
    )
    print(f"Status: {resp.status_code}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    print(
        f"Access token: {body['access_token'][:20]}... | "
        f"Refresh: {body['refresh_token'][:20]}..."
    )
    print("PASS")
    return body["refresh_token"]


async def test_exchange_consultant_channel(client: httpx.AsyncClient) -> None:
    """Consultant channel also gets a refresh token (uniform response shape)."""
    print("\n--- Test 2: Exchange consultant channel ---")
    resp = await client.post(
        EXCHANGE_URL,
        json={
            "supabase_jwt": _supabase_jwt(),
            "tenant_id": TEST_TENANT_ID,
            "email": TEST_EMAIL,
            "channel": "consultant",
        },
    )
    print(f"Status: {resp.status_code}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["refresh_token"]
    print("PASS")


async def test_exchange_invalid_supabase_jwt(client: httpx.AsyncClient) -> None:
    """Invalid Supabase JWT signature -> 401."""
    print("\n--- Test 3: Invalid Supabase JWT ---")
    bad = jwt.encode(
        {
            "sub": TEST_SUPABASE_SUB,
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
        },
        "wrong-secret",
        algorithm="HS256",
    )
    resp = await client.post(
        EXCHANGE_URL,
        json={
            "supabase_jwt": bad,
            "tenant_id": TEST_TENANT_ID,
            "email": TEST_EMAIL,
            "channel": "cli",
        },
    )
    print(f"Status: {resp.status_code}")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
    print("PASS")


async def test_exchange_missing_tenant_id(client: httpx.AsyncClient) -> None:
    """Missing tenant_id -> 400."""
    print("\n--- Test 4: Missing tenant_id ---")
    resp = await client.post(
        EXCHANGE_URL,
        json={
            "supabase_jwt": _supabase_jwt(),
            "tenant_id": "",
            "email": TEST_EMAIL,
            "channel": "cli",
        },
    )
    print(f"Status: {resp.status_code}")
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
    print("PASS")


async def test_exchange_expired_supabase_token(client: httpx.AsyncClient) -> None:
    """Expired Supabase JWT -> 401."""
    print("\n--- Test 5: Expired Supabase JWT ---")
    resp = await client.post(
        EXCHANGE_URL,
        json={
            "supabase_jwt": _supabase_jwt(expired=True),
            "tenant_id": TEST_TENANT_ID,
            "email": TEST_EMAIL,
            "channel": "cli",
        },
    )
    print(f"Status: {resp.status_code}")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
    print("PASS")


async def test_refresh_round_trip(
    client: httpx.AsyncClient, raw_refresh: str
) -> None:
    """Use a freshly issued refresh token -> 200 + new pair."""
    print("\n--- Test 6: Refresh round-trip ---")
    resp = await client.post(REFRESH_URL, json={"refresh_token": raw_refresh})
    print(f"Status: {resp.status_code}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["refresh_token"] != raw_refresh, "Refresh token must rotate"
    print("PASS")


async def test_refresh_reused_token_fails(
    client: httpx.AsyncClient, raw_refresh: str
) -> None:
    """Re-using a rotated refresh token -> 401."""
    print("\n--- Test 7: Reused refresh token ---")
    resp = await client.post(REFRESH_URL, json={"refresh_token": raw_refresh})
    print(f"Status: {resp.status_code}")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
    print("PASS")


async def test_refresh_garbage_token(client: httpx.AsyncClient) -> None:
    """Garbage refresh token -> 401."""
    print("\n--- Test 8: Garbage refresh token ---")
    resp = await client.post(REFRESH_URL, json={"refresh_token": "not-a-real-token"})
    print(f"Status: {resp.status_code}")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
    print("PASS")


async def main():
    print("=" * 60)
    print("Auth Exchange / Refresh API Tests")
    print(f"Server: {BASE_URL}")
    print("=" * 60)

    if not SUPABASE_SECRET:
        print(
            "\nERROR: SUPABASE_JWT_SECRET environment variable not set. "
            "Set it to the same value the server uses, then re-run."
        )
        return

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

        try:
            raw_refresh = await test_exchange_happy_path(client)
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {e}")
            failed += 1
            raw_refresh = ""

        independent = [
            test_exchange_consultant_channel,
            test_exchange_invalid_supabase_jwt,
            test_exchange_missing_tenant_id,
            test_exchange_expired_supabase_token,
            test_refresh_garbage_token,
        ]
        for test in independent:
            try:
                await test(client)
                passed += 1
            except AssertionError as e:
                print(f"FAIL: {e}")
                failed += 1
            except Exception as e:
                print(f"ERROR: {e}")
                failed += 1

        if raw_refresh:
            try:
                await test_refresh_round_trip(client, raw_refresh)
                passed += 1
                # After rotation, the original token is invalid
                try:
                    await test_refresh_reused_token_fails(client, raw_refresh)
                    passed += 1
                except AssertionError as e:
                    print(f"FAIL: {e}")
                    failed += 1
            except AssertionError as e:
                print(f"FAIL: {e}")
                failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
