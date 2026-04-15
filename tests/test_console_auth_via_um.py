"""Unit tests for the UM-delegated console auth path.

Covers:
- `_sanitize_next` open-redirect guard
- `build_access_token_from_claims` — identity-only JWT
- `_authenticate_console_via_um` — URL-anchor auth + UM delegation
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt

from server.core.config import settings


# ---------------------------------------------------------------------------
# _sanitize_next
# ---------------------------------------------------------------------------


class TestSanitizeNext:
    def test_valid_console_path(self):
        from server.auth.router import _sanitize_next

        assert _sanitize_next("/console/4") == "/console/4"
        assert _sanitize_next("/console/4?tab=apps") == "/console/4?tab=apps"

    def test_rejects_absolute_url(self):
        from server.auth.router import _sanitize_next

        assert _sanitize_next("https://evil.com/console/4") == ""

    def test_rejects_protocol_relative(self):
        from server.auth.router import _sanitize_next

        assert _sanitize_next("//evil.com/path") == ""

    def test_rejects_non_console_path(self):
        from server.auth.router import _sanitize_next

        assert _sanitize_next("/admin") == ""
        assert _sanitize_next("/api/v1/auth/me") == ""

    def test_rejects_none_and_empty(self):
        from server.auth.router import _sanitize_next

        assert _sanitize_next(None) == ""
        assert _sanitize_next("") == ""

    def test_rejects_crlf_smuggling(self):
        from server.auth.router import _sanitize_next

        assert _sanitize_next("/console/4\r\nX: y") == ""
        assert _sanitize_next("/console/4\\foo") == ""


# ---------------------------------------------------------------------------
# build_access_token_from_claims
# ---------------------------------------------------------------------------


class TestBuildAccessTokenFromClaims:
    def test_identity_only_claims(self):
        from server.auth.service import build_access_token_from_claims

        token = build_access_token_from_claims(
            {"sub": "google-123", "email": "a@b.com", "channel": "console"}
        )
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        assert payload["sub"] == "google-123"
        assert payload["email"] == "a@b.com"
        assert payload["channel"] == "console"
        assert payload["type"] == "access"
        assert "tenant_id" not in payload
        assert "role" not in payload
        assert "exp" in payload


# ---------------------------------------------------------------------------
# _authenticate_console_via_um
# ---------------------------------------------------------------------------


class TestAuthenticateConsoleViaUm:
    @pytest.mark.asyncio
    async def test_allows_when_um_says_yes(self):
        from server.console.router import _authenticate_console_via_um

        request = MagicMock()
        request.headers = {"x-tenant-id": "3"}
        db = AsyncMock()

        tenant_row = MagicMock()
        tenant_row.id = "3"
        scalar = MagicMock()
        scalar.scalar_one_or_none = MagicMock(return_value=tenant_row)
        db.execute = AsyncMock(return_value=scalar)

        with patch(
            "server.auth.platform_access_service.has_tenant_access",
            AsyncMock(return_value=True),
        ), patch(
            "server.console.router.set_tenant_context", AsyncMock()
        ):
            tenant, identity, _db = await _authenticate_console_via_um(
                payload={"sub": "g1", "email": "a@b.com"},
                request=request,
                db=db,
                allow_redirect=False,
                url_tenant_id="3",
            )

        assert tenant is tenant_row
        assert identity.sub == "g1"
        assert identity.email == "a@b.com"
        assert identity.id == "g1"

    @pytest.mark.asyncio
    async def test_forbidden_when_um_says_no(self):
        from server.console.router import _authenticate_console_via_um

        request = MagicMock()
        request.headers = {}
        db = AsyncMock()

        with patch(
            "server.auth.platform_access_service.has_tenant_access",
            AsyncMock(return_value=False),
        ):
            with pytest.raises(HTTPException) as exc:
                await _authenticate_console_via_um(
                    payload={"sub": "g1", "email": "a@b.com"},
                    request=request,
                    db=db,
                    allow_redirect=False,
                    url_tenant_id="3",
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_email_rejects(self):
        from server.console.router import _authenticate_console_via_um

        request = MagicMock()
        request.headers = {}
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await _authenticate_console_via_um(
                payload={"sub": "g1", "email": ""},
                request=request,
                db=db,
                allow_redirect=False,
                url_tenant_id="3",
            )
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_tenant_rejects(self):
        from server.console.router import _authenticate_console_via_um

        request = MagicMock()
        request.headers = {}
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await _authenticate_console_via_um(
                payload={"sub": "g1", "email": "a@b.com"},
                request=request,
                db=db,
                allow_redirect=False,
                url_tenant_id=None,
            )
        assert exc.value.status_code == 401
