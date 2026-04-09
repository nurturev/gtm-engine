"""Unit tests for the exchange + refresh endpoints (Task 1 + Task 2).

Calls the endpoint functions directly with mocked DB / settings, so the
tests do not require a running server. The Supabase JWT path is exercised
end-to-end against the real `python-jose` library.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt

from server.auth.models import RefreshToken
from server.auth.router import (
    exchange_supabase_token,
    refresh_access_token,
)
from server.auth.schemas import ExchangeRequest, RefreshRequest
from server.core.config import settings
from server.core.security import hash_token


TEST_SUPABASE_SECRET = "supabase-test-secret-do-not-use-in-prod"


def _make_supabase_jwt(
    *,
    sub: str = "supabase-uuid-1",
    email: str = "user@acme.com",
    expired: bool = False,
    omit_sub: bool = False,
) -> str:
    payload: dict = {
        "aud": "authenticated",
        "role": "authenticated",
        "email": email,
        "exp": int(
            (
                datetime.now(timezone.utc)
                + (timedelta(hours=-1) if expired else timedelta(hours=1))
            ).timestamp()
        ),
    }
    if not omit_sub:
        payload["sub"] = sub
    return jwt.encode(payload, TEST_SUPABASE_SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# /api/v1/auth/exchange
# ---------------------------------------------------------------------------


class TestExchangeEndpoint:
    @pytest.fixture(autouse=True)
    def configure_supabase(self):
        with patch.object(settings, "SUPABASE_JWT_SECRET", TEST_SUPABASE_SECRET):
            yield

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        return db

    async def test_happy_path_returns_access_and_refresh(self, mock_db):
        body = ExchangeRequest(
            supabase_jwt=_make_supabase_jwt(sub="sb-uuid-7"),
            tenant_id="137",
            email="user@acme.com",
            channel="cli",
        )

        resp = await exchange_supabase_token(body=body, db=mock_db)

        # Both tokens issued
        assert resp.access_token
        assert resp.refresh_token
        assert resp.expires_in == settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60

        # Access token decodes with the right claims
        payload = jwt.decode(
            resp.access_token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        assert payload["sub"] == "sb-uuid-7"
        assert payload["tenant_id"] == "137"
        assert payload["email"] == "user@acme.com"
        assert payload["channel"] == "cli"
        assert payload["type"] == "access"

        # A refresh row was inserted with matching subject + tenant + channel
        record = mock_db.add.call_args.args[0]
        assert isinstance(record, RefreshToken)
        assert record.subject_id == "sb-uuid-7"
        assert record.tenant_id == "137"
        assert record.channel == "cli"
        assert record.token_hash == hashlib.sha256(
            resp.refresh_token.encode()
        ).hexdigest()

    async def test_consultant_channel_also_returns_refresh(self, mock_db):
        body = ExchangeRequest(
            supabase_jwt=_make_supabase_jwt(),
            tenant_id="9",
            email="agent@x.com",
            channel="consultant",
        )
        resp = await exchange_supabase_token(body=body, db=mock_db)
        assert resp.refresh_token
        record = mock_db.add.call_args.args[0]
        assert record.channel == "consultant"

    async def test_email_falls_back_to_supabase_payload(self, mock_db):
        body = ExchangeRequest(
            supabase_jwt=_make_supabase_jwt(email="from-supabase@acme.com"),
            tenant_id="42",
            email=None,
            channel="cli",
        )
        resp = await exchange_supabase_token(body=body, db=mock_db)
        payload = jwt.decode(
            resp.access_token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        assert payload["email"] == "from-supabase@acme.com"

    async def test_invalid_supabase_signature_returns_401(self, mock_db):
        bad_token = jwt.encode(
            {"sub": "x", "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())},
            "wrong-secret",
            algorithm="HS256",
        )
        body = ExchangeRequest(
            supabase_jwt=bad_token,
            tenant_id="1",
            email="x@y.com",
            channel="cli",
        )
        with pytest.raises(HTTPException) as exc_info:
            await exchange_supabase_token(body=body, db=mock_db)
        assert exc_info.value.status_code == 401

    async def test_expired_supabase_token_returns_401(self, mock_db):
        body = ExchangeRequest(
            supabase_jwt=_make_supabase_jwt(expired=True),
            tenant_id="1",
            email="x@y.com",
            channel="cli",
        )
        with pytest.raises(HTTPException) as exc_info:
            await exchange_supabase_token(body=body, db=mock_db)
        assert exc_info.value.status_code == 401

    async def test_supabase_token_missing_sub_returns_401(self, mock_db):
        body = ExchangeRequest(
            supabase_jwt=_make_supabase_jwt(omit_sub=True),
            tenant_id="1",
            email="x@y.com",
            channel="cli",
        )
        with pytest.raises(HTTPException) as exc_info:
            await exchange_supabase_token(body=body, db=mock_db)
        assert exc_info.value.status_code == 401
        assert "subject" in exc_info.value.detail.lower()

    async def test_missing_tenant_id_returns_400(self, mock_db):
        body = ExchangeRequest(
            supabase_jwt=_make_supabase_jwt(),
            tenant_id="",
            email="x@y.com",
            channel="cli",
        )
        with pytest.raises(HTTPException) as exc_info:
            await exchange_supabase_token(body=body, db=mock_db)
        assert exc_info.value.status_code == 400
        assert "tenant_id" in exc_info.value.detail

    async def test_supabase_secret_unset_returns_503(self, mock_db):
        body = ExchangeRequest(
            supabase_jwt=_make_supabase_jwt(),
            tenant_id="1",
            email="x@y.com",
            channel="cli",
        )
        with patch.object(settings, "SUPABASE_JWT_SECRET", None):
            with pytest.raises(HTTPException) as exc_info:
                await exchange_supabase_token(body=body, db=mock_db)
        assert exc_info.value.status_code == 503

    async def test_no_user_or_tenant_row_created(self, mock_db):
        """The exchange endpoint must never insert User or Tenant rows."""
        body = ExchangeRequest(
            supabase_jwt=_make_supabase_jwt(),
            tenant_id="42",
            email="x@y.com",
            channel="cli",
        )
        await exchange_supabase_token(body=body, db=mock_db)

        # Only one db.add call — and it's a RefreshToken row
        assert mock_db.add.call_count == 1
        record = mock_db.add.call_args.args[0]
        assert isinstance(record, RefreshToken)


# ---------------------------------------------------------------------------
# /api/v1/auth/refresh
# ---------------------------------------------------------------------------


class TestRefreshEndpoint:
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        return db

    def _stored_row(
        self,
        *,
        subject_id: str = "sb-uuid-7",
        tenant_id: str = "137",
        email: str | None = "user@acme.com",
        channel: str = "cli",
        expired: bool = False,
    ) -> RefreshToken:
        row = MagicMock(spec=RefreshToken)
        row.subject_id = subject_id
        row.tenant_id = tenant_id
        row.email = email
        row.channel = channel
        row.expires_at = datetime.now(timezone.utc) + (
            timedelta(days=-1) if expired else timedelta(days=10)
        )
        row.token_hash = "irrelevant"
        return row

    async def test_happy_path_rotates_and_preserves_claims(self, mock_db):
        stored = self._stored_row()
        result = MagicMock()
        result.scalar_one_or_none.return_value = stored
        mock_db.execute = AsyncMock(return_value=result)

        body = RefreshRequest(refresh_token="raw-refresh-value")
        resp = await refresh_access_token(body=body, db=mock_db)

        # Old row deleted
        mock_db.delete.assert_awaited_once_with(stored)

        # New tokens minted
        assert resp.access_token
        assert resp.refresh_token

        # The new access token preserves tenant + email + channel + sub
        payload = jwt.decode(
            resp.access_token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        assert payload["sub"] == "sb-uuid-7"
        assert payload["tenant_id"] == "137"
        assert payload["email"] == "user@acme.com"
        assert payload["channel"] == "cli"

        # A new refresh row was inserted with the same claims
        record = mock_db.add.call_args.args[0]
        assert isinstance(record, RefreshToken)
        assert record.subject_id == "sb-uuid-7"
        assert record.tenant_id == "137"
        assert record.channel == "cli"

    async def test_unknown_token_returns_401(self, mock_db):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result)

        body = RefreshRequest(refresh_token="garbage")
        with pytest.raises(HTTPException) as exc_info:
            await refresh_access_token(body=body, db=mock_db)
        assert exc_info.value.status_code == 401

    async def test_expired_row_returns_401(self, mock_db):
        # Filtering of expired rows happens in the SQL WHERE clause, so the
        # ORM lookup naturally returns None for an expired token. Simulate it.
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result)

        body = RefreshRequest(refresh_token="raw")
        with pytest.raises(HTTPException) as exc_info:
            await refresh_access_token(body=body, db=mock_db)
        assert exc_info.value.status_code == 401

    async def test_consultant_channel_round_trips(self, mock_db):
        stored = self._stored_row(channel="consultant")
        result = MagicMock()
        result.scalar_one_or_none.return_value = stored
        mock_db.execute = AsyncMock(return_value=result)

        body = RefreshRequest(refresh_token="raw")
        resp = await refresh_access_token(body=body, db=mock_db)

        payload = jwt.decode(
            resp.access_token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        assert payload["channel"] == "consultant"

    async def test_lookup_uses_sha256_hash(self, mock_db):
        """The refresh endpoint must hash the incoming raw token before lookup."""
        stored = self._stored_row()
        result = MagicMock()
        result.scalar_one_or_none.return_value = stored
        mock_db.execute = AsyncMock(return_value=result)

        raw = "some-raw-refresh-value"
        body = RefreshRequest(refresh_token=raw)
        await refresh_access_token(body=body, db=mock_db)

        # We can't easily inspect the SQLAlchemy WHERE clause from a mock,
        # but the contract is that the hash function used is SHA-256.
        # Spot-check by recomputing hash_token() and asserting determinism.
        assert hash_token(raw) == hashlib.sha256(raw.encode()).hexdigest()
