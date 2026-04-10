"""Unit tests for the refactored auth service helpers (Task 1 + Task 2).

Covers:
    * `build_access_token` — pure JWT encoding helper
    * `persist_refresh_token` — refresh-row persistence helper
    * `generate_tokens` — legacy wrapper still used by Google OAuth path
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from jose import jwt

from server.auth.models import RefreshToken, User
from server.auth.service import (
    build_access_token,
    generate_tokens,
    persist_refresh_token,
)
from server.core.config import settings


# ---------------------------------------------------------------------------
# build_access_token
# ---------------------------------------------------------------------------


class TestBuildAccessToken:
    def test_happy_path_contains_required_claims(self):
        token = build_access_token(
            subject_id="supabase-uuid-1",
            tenant_id="137",
            email="user@acme.com",
            channel="cli",
        )
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        assert payload["sub"] == "supabase-uuid-1"
        assert payload["tenant_id"] == "137"
        assert payload["email"] == "user@acme.com"
        assert payload["channel"] == "cli"
        assert payload["type"] == "access"
        assert "exp" in payload
        # `role` is only set when the legacy path passes it
        assert "role" not in payload

    def test_role_is_included_when_provided(self):
        token = build_access_token(
            subject_id="user-1",
            tenant_id="42",
            email="x@y.com",
            channel="console",
            role="owner",
        )
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        assert payload["role"] == "owner"

    def test_email_none_becomes_empty_string(self):
        token = build_access_token(
            subject_id="user-1",
            tenant_id="42",
            email=None,
            channel="cli",
        )
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        assert payload["email"] == ""

    def test_consultant_channel(self):
        token = build_access_token(
            subject_id="agent-sub",
            tenant_id="9",
            email="agent@x.com",
            channel="consultant",
        )
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        assert payload["channel"] == "consultant"


# ---------------------------------------------------------------------------
# persist_refresh_token
# ---------------------------------------------------------------------------


class TestPersistRefreshToken:
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        return db

    async def test_returns_raw_token_and_persists_hash(self, mock_db):
        raw = await persist_refresh_token(
            mock_db,
            subject_id="supabase-uuid-1",
            tenant_id="137",
            email="user@acme.com",
            channel="cli",
        )

        # Returned value is a non-empty string
        assert isinstance(raw, str) and len(raw) > 30

        # db.add was called with a RefreshToken bound to the right values
        mock_db.add.assert_called_once()
        record = mock_db.add.call_args.args[0]
        assert isinstance(record, RefreshToken)
        assert record.subject_id == "supabase-uuid-1"
        assert record.tenant_id == "137"
        assert record.email == "user@acme.com"
        assert record.channel == "cli"

        # The persisted hash matches SHA-256 of the returned raw token
        assert record.token_hash == hashlib.sha256(raw.encode()).hexdigest()

        # expires_at is in the future
        assert record.expires_at > datetime.now(timezone.utc)

        mock_db.commit.assert_awaited_once()

    async def test_each_call_returns_unique_token(self, mock_db):
        a = await persist_refresh_token(
            mock_db, subject_id="s", tenant_id="t", email=None, channel="cli"
        )
        b = await persist_refresh_token(
            mock_db, subject_id="s", tenant_id="t", email=None, channel="cli"
        )
        assert a != b

    async def test_consultant_channel_persists_correctly(self, mock_db):
        await persist_refresh_token(
            mock_db,
            subject_id="agent",
            tenant_id="9",
            email="a@b.com",
            channel="consultant",
        )
        record = mock_db.add.call_args.args[0]
        assert record.channel == "consultant"

    async def test_email_none_persisted_as_none(self, mock_db):
        await persist_refresh_token(
            mock_db,
            subject_id="s",
            tenant_id="t",
            email=None,
            channel="cli",
        )
        record = mock_db.add.call_args.args[0]
        assert record.email is None


# ---------------------------------------------------------------------------
# generate_tokens (legacy wrapper for Google OAuth callback)
# ---------------------------------------------------------------------------


class TestGenerateTokens:
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        return db

    async def test_legacy_wrapper_persists_subject_as_user_id(self, mock_db):
        user = MagicMock(spec=User)
        user.id = "user_abc"
        user.tenant_id = "tn_42"
        user.email = "x@y.com"
        user.role = "owner"

        result = await generate_tokens(mock_db, user)

        assert "access_token" in result
        assert "refresh_token" in result

        record = mock_db.add.call_args.args[0]
        # Legacy path stamps subject_id with the local user id and uses
        # the "console" channel.
        assert record.subject_id == "user_abc"
        assert record.tenant_id == "tn_42"
        assert record.channel == "console"

        payload = jwt.decode(
            result["access_token"],
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        assert payload["sub"] == "user_abc"
        assert payload["tenant_id"] == "tn_42"
        assert payload["role"] == "owner"
        assert payload["channel"] == "console"
