"""Tests for pseudo-tenant sync endpoints (server/auth/tenant_router.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt

from server.auth.dependencies import require_service_token
from server.auth.tenant_router import (
    CreateTenantRequest,
    UpdateTenantRequest,
    create_tenant,
    update_tenant,
)
from server.core.config import settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_SERVICE_TOKEN = "test-svc-token-abc123"
TEST_JWT_SECRET = settings.JWT_SECRET_KEY


def _make_jwt() -> str:
    """Create a valid gtm-engine JWT for testing."""
    payload = {
        "sub": "user-uuid-1",
        "tenant_id": "42",
        "email": "test@acme.com",
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _mock_tenant(id: str = "528", name: str = "Acme Corp", domain: str = "acme.com") -> MagicMock:
    tenant = MagicMock()
    tenant.id = id
    tenant.name = name
    tenant.domain = domain
    tenant.created_at = datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc)
    return tenant


# ---------------------------------------------------------------------------
# require_service_token dependency
# ---------------------------------------------------------------------------


class TestRequireServiceToken:
    @pytest.fixture(autouse=True)
    def enable_service_token(self):
        with patch.object(settings, "GTM_ENGINE_SERVICE_TOKEN", TEST_SERVICE_TOKEN):
            yield

    async def test_valid_service_token_passes(self):
        await require_service_token(authorization=f"Bearer {TEST_SERVICE_TOKEN}")

    async def test_valid_jwt_returns_403(self):
        token = _make_jwt()
        with pytest.raises(HTTPException) as exc_info:
            await require_service_token(authorization=f"Bearer {token}")
        assert exc_info.value.status_code == 403
        assert "service token" in exc_info.value.detail

    async def test_wrong_token_returns_403(self):
        with pytest.raises(HTTPException) as exc_info:
            await require_service_token(authorization="Bearer wrong-token")
        assert exc_info.value.status_code == 403

    async def test_no_bearer_prefix_returns_401(self):
        with pytest.raises(HTTPException) as exc_info:
            await require_service_token(authorization="Basic abc123")
        assert exc_info.value.status_code == 401
        assert "Bearer" in exc_info.value.detail

    async def test_service_token_not_configured_returns_403(self):
        with patch.object(settings, "GTM_ENGINE_SERVICE_TOKEN", None):
            with pytest.raises(HTTPException) as exc_info:
                await require_service_token(authorization=f"Bearer {TEST_SERVICE_TOKEN}")
            assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


class TestCreateTenantValidation:
    def test_valid_request(self):
        req = CreateTenantRequest(id="528", name="Acme Corp", domain="acme.com")
        assert req.id == "528"

    def test_non_numeric_id_rejected(self):
        with pytest.raises(ValueError, match="numeric"):
            CreateTenantRequest(id="tn_abc", name="Acme", domain="acme.com")

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError):
            CreateTenantRequest(id="", name="Acme", domain="acme.com")

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError):
            CreateTenantRequest(id="1", name="  ", domain="acme.com")

    def test_empty_domain_rejected(self):
        with pytest.raises(ValueError):
            CreateTenantRequest(id="1", name="Acme", domain="")


class TestUpdateTenantValidation:
    def test_valid_name_only(self):
        req = UpdateTenantRequest(name="New Name")
        assert req.name == "New Name"
        assert req.domain is None

    def test_valid_domain_only(self):
        req = UpdateTenantRequest(domain="new.com")
        assert req.domain == "new.com"

    def test_empty_body_rejected(self):
        with pytest.raises(ValueError, match="At least one field"):
            UpdateTenantRequest()

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError):
            UpdateTenantRequest(name="")


# ---------------------------------------------------------------------------
# create_tenant endpoint
# ---------------------------------------------------------------------------


class TestCreateTenantEndpoint:
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        return db

    async def test_create_returns_tenant(self, mock_db):
        tenant = _mock_tenant()
        mock_db.refresh = AsyncMock(return_value=None)
        mock_db.commit = AsyncMock(return_value=None)

        # After commit + refresh, the tenant object is populated
        async def fake_refresh(t):
            t.id = "528"
            t.name = "Acme Corp"
            t.domain = "acme.com"
            t.created_at = datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc)

        mock_db.refresh = AsyncMock(side_effect=fake_refresh)

        body = CreateTenantRequest(id="528", name="Acme Corp", domain="acme.com")
        response_obj = MagicMock()

        result = await create_tenant(body=body, response=response_obj, db=mock_db)
        assert result.id == "528"
        assert result.name == "Acme Corp"
        assert result.domain == "acme.com"
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()

    async def test_duplicate_returns_existing(self, mock_db):
        from sqlalchemy.exc import IntegrityError

        mock_db.commit = AsyncMock(side_effect=IntegrityError("", [], Exception()))
        mock_db.rollback = AsyncMock()

        existing = _mock_tenant()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = existing
        mock_db.execute = AsyncMock(return_value=mock_result)

        body = CreateTenantRequest(id="528", name="Acme Corp", domain="acme.com")
        response_obj = MagicMock()

        result = await create_tenant(body=body, response=response_obj, db=mock_db)
        assert result.id == "528"
        response_obj.status_code = 200
        mock_db.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# update_tenant endpoint
# ---------------------------------------------------------------------------


class TestUpdateTenantEndpoint:
    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    async def test_update_existing_tenant(self, mock_db):
        tenant = _mock_tenant()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = tenant
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.refresh = AsyncMock(return_value=None)

        body = UpdateTenantRequest(name="Acme Corporation")
        result = await update_tenant(body=body, tenant_id="528", db=mock_db)

        assert tenant.name == "Acme Corporation"
        mock_db.commit.assert_awaited_once()

    async def test_update_nonexistent_returns_404(self, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        body = UpdateTenantRequest(domain="new.com")

        with pytest.raises(HTTPException) as exc_info:
            await update_tenant(body=body, tenant_id="999", db=mock_db)

        assert exc_info.value.status_code == 404
        assert "999" in exc_info.value.detail

    async def test_update_only_provided_fields(self, mock_db):
        tenant = _mock_tenant()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = tenant
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.refresh = AsyncMock(return_value=None)

        body = UpdateTenantRequest(domain="new.com")
        await update_tenant(body=body, tenant_id="528", db=mock_db)

        assert tenant.domain == "new.com"
        assert tenant.name == "Acme Corp"  # unchanged
