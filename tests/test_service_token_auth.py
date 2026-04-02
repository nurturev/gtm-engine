"""Unit tests for service token authentication in server/auth/dependencies.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi import HTTPException
from jose import jwt

from server.auth.dependencies import (
    TenantRef,
    _is_service_token,
    get_tenant_from_token,
)
from server.core.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_SERVICE_TOKEN = "test-svc-token-abc123"
TEST_TENANT_ID = "137"
TEST_JWT_SECRET = settings.JWT_SECRET_KEY


def _make_jwt(tenant_id: str = TEST_TENANT_ID, sub: str = "user-uuid-1") -> str:
    """Create a valid gtm-engine JWT for testing."""
    payload = {
        "sub": sub,
        "tenant_id": tenant_id,
        "email": "test@acme.com",
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _mock_request(headers: dict[str, str] | None = None) -> MagicMock:
    """Create a mock FastAPI Request with given headers."""
    req = MagicMock()
    req.headers = headers or {}
    return req


# ---------------------------------------------------------------------------
# _is_service_token
# ---------------------------------------------------------------------------


class TestIsServiceToken:
    def test_matches_configured_token(self):
        with patch.object(settings, "GTM_ENGINE_SERVICE_TOKEN", TEST_SERVICE_TOKEN):
            assert _is_service_token(TEST_SERVICE_TOKEN) is True

    def test_rejects_wrong_token(self):
        with patch.object(settings, "GTM_ENGINE_SERVICE_TOKEN", TEST_SERVICE_TOKEN):
            assert _is_service_token("wrong-token") is False

    def test_returns_false_when_not_configured(self):
        with patch.object(settings, "GTM_ENGINE_SERVICE_TOKEN", None):
            assert _is_service_token(TEST_SERVICE_TOKEN) is False

    def test_rejects_empty_string(self):
        with patch.object(settings, "GTM_ENGINE_SERVICE_TOKEN", TEST_SERVICE_TOKEN):
            assert _is_service_token("") is False

    def test_rejects_partial_match(self):
        with patch.object(settings, "GTM_ENGINE_SERVICE_TOKEN", TEST_SERVICE_TOKEN):
            assert _is_service_token(TEST_SERVICE_TOKEN[:10]) is False


# ---------------------------------------------------------------------------
# get_tenant_from_token — service token path
# ---------------------------------------------------------------------------


class TestServiceTokenAuth:
    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture(autouse=True)
    def enable_service_token(self):
        with patch.object(settings, "GTM_ENGINE_SERVICE_TOKEN", TEST_SERVICE_TOKEN):
            yield

    async def test_service_token_returns_tenant_ref(self, mock_db):
        request = _mock_request({"X-Agent-Type": "consultant", "X-Thread-Id": "t-1"})

        with patch("server.auth.dependencies.set_tenant_context", new_callable=AsyncMock) as mock_ctx:
            result = await get_tenant_from_token(
                request=request,
                authorization=f"Bearer {TEST_SERVICE_TOKEN}",
                db=mock_db,
                x_tenant_id=TEST_TENANT_ID,
            )

        assert isinstance(result, TenantRef)
        assert result.id == TEST_TENANT_ID
        mock_ctx.assert_awaited_once_with(mock_db, TEST_TENANT_ID)

    async def test_service_token_missing_tenant_id_returns_400(self, mock_db):
        request = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await get_tenant_from_token(
                request=request,
                authorization=f"Bearer {TEST_SERVICE_TOKEN}",
                db=mock_db,
                x_tenant_id=None,
            )

        assert exc_info.value.status_code == 400
        assert "X-Tenant-Id" in exc_info.value.detail

    async def test_service_token_empty_tenant_id_returns_400(self, mock_db):
        request = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await get_tenant_from_token(
                request=request,
                authorization=f"Bearer {TEST_SERVICE_TOKEN}",
                db=mock_db,
                x_tenant_id="",
            )

        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# get_tenant_from_token — JWT path
# ---------------------------------------------------------------------------


class TestJWTAuth:
    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture(autouse=True)
    def enable_service_token(self):
        with patch.object(settings, "GTM_ENGINE_SERVICE_TOKEN", TEST_SERVICE_TOKEN):
            yield

    async def test_valid_jwt_returns_tenant_ref(self, mock_db):
        token = _make_jwt(tenant_id="42")
        request = _mock_request()

        with patch("server.auth.dependencies.set_tenant_context", new_callable=AsyncMock) as mock_ctx:
            result = await get_tenant_from_token(
                request=request,
                authorization=f"Bearer {token}",
                db=mock_db,
                x_tenant_id=None,
            )

        assert isinstance(result, TenantRef)
        assert result.id == "42"
        mock_ctx.assert_awaited_once_with(mock_db, "42")

    async def test_jwt_ignores_x_tenant_id_header(self, mock_db):
        """JWT path reads tenant_id from claims, not from X-Tenant-Id header."""
        token = _make_jwt(tenant_id="42")
        request = _mock_request()

        with patch("server.auth.dependencies.set_tenant_context", new_callable=AsyncMock) as mock_ctx:
            result = await get_tenant_from_token(
                request=request,
                authorization=f"Bearer {token}",
                db=mock_db,
                x_tenant_id="999",  # should be ignored
            )

        assert result.id == "42"
        mock_ctx.assert_awaited_once_with(mock_db, "42")

    async def test_invalid_jwt_returns_401(self, mock_db):
        request = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await get_tenant_from_token(
                request=request,
                authorization="Bearer not-a-valid-jwt",
                db=mock_db,
                x_tenant_id=None,
            )

        assert exc_info.value.status_code == 401

    async def test_jwt_missing_tenant_id_claim_returns_401(self, mock_db):
        """JWT without tenant_id in claims should be rejected."""
        payload = {
            "sub": "user-1",
            "email": "test@acme.com",
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(hours=24),
        }
        token = jwt.encode(payload, TEST_JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
        request = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await get_tenant_from_token(
                request=request,
                authorization=f"Bearer {token}",
                db=mock_db,
                x_tenant_id=None,
            )

        assert exc_info.value.status_code == 401
        assert "tenant_id" in exc_info.value.detail


# ---------------------------------------------------------------------------
# get_tenant_from_token — common error cases
# ---------------------------------------------------------------------------


class TestAuthErrors:
    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    async def test_missing_bearer_prefix_returns_401(self, mock_db):
        request = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await get_tenant_from_token(
                request=request,
                authorization="Basic abc123",
                db=mock_db,
                x_tenant_id=None,
            )

        assert exc_info.value.status_code == 401
        assert "Bearer" in exc_info.value.detail

    async def test_wrong_token_falls_through_to_jwt_401(self, mock_db):
        """A token that doesn't match service token AND isn't a valid JWT → 401."""
        with patch.object(settings, "GTM_ENGINE_SERVICE_TOKEN", TEST_SERVICE_TOKEN):
            request = _mock_request()

            with pytest.raises(HTTPException) as exc_info:
                await get_tenant_from_token(
                    request=request,
                    authorization="Bearer wrong-token",
                    db=mock_db,
                    x_tenant_id=TEST_TENANT_ID,
                )

            assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# get_tenant_from_token — service token disabled
# ---------------------------------------------------------------------------


class TestServiceTokenDisabled:
    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture(autouse=True)
    def disable_service_token(self):
        with patch.object(settings, "GTM_ENGINE_SERVICE_TOKEN", None):
            yield

    async def test_service_token_value_treated_as_jwt_when_disabled(self, mock_db):
        """When GTM_ENGINE_SERVICE_TOKEN is not set, even the 'right' token
        falls through to JWT decode (and fails because it's not a JWT)."""
        request = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await get_tenant_from_token(
                request=request,
                authorization=f"Bearer {TEST_SERVICE_TOKEN}",
                db=mock_db,
                x_tenant_id=TEST_TENANT_ID,
            )

        assert exc_info.value.status_code == 401

    async def test_jwt_still_works_when_service_token_disabled(self, mock_db):
        token = _make_jwt(tenant_id="55")
        request = _mock_request()

        with patch("server.auth.dependencies.set_tenant_context", new_callable=AsyncMock):
            result = await get_tenant_from_token(
                request=request,
                authorization=f"Bearer {token}",
                db=mock_db,
                x_tenant_id=None,
            )

        assert result.id == "55"
