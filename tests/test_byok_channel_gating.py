"""Unit tests for BYOK channel gating (Task 4).

Verifies that service token requests skip BYOK key lookup and always use
platform keys, while JWT requests continue to prefer BYOK keys.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from server.auth.dependencies import TenantRef
from server.core.exceptions import ProviderError


# ---------------------------------------------------------------------------
# TenantRef.is_service_token
# ---------------------------------------------------------------------------


class TestTenantRefIsServiceToken:
    def test_default_is_false(self):
        ref = TenantRef(id="123")
        assert ref.is_service_token is False

    def test_service_token_path(self):
        ref = TenantRef(id="123", is_service_token=True)
        assert ref.is_service_token is True

    def test_jwt_path(self):
        ref = TenantRef(id="123", is_service_token=False)
        assert ref.is_service_token is False


# ---------------------------------------------------------------------------
# resolve_api_key with skip_byok
# ---------------------------------------------------------------------------


class TestResolveApiKeySkipByok:
    @pytest.mark.asyncio
    async def test_skip_byok_true_uses_platform_key(self):
        """When skip_byok=True and tenant has BYOK key, platform key is used."""
        from server.execution.service import resolve_api_key

        mock_db = AsyncMock()

        with patch("server.execution.service._PLATFORM_KEYS", {"apollo": "platform-key-123"}):
            api_key, is_byok = await resolve_api_key(
                mock_db, "tenant-1", "apollo", skip_byok=True
            )

        assert api_key == "platform-key-123"
        assert is_byok is False
        # DB should NOT be queried for BYOK keys
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_byok_false_returns_byok_key(self):
        """When skip_byok=False and tenant has BYOK key, BYOK key is used."""
        from server.execution.service import resolve_api_key

        mock_byok = MagicMock()
        mock_byok.encrypted_key = "encrypted-byok-key"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_byok

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("server.execution.service.decrypt_key", return_value="decrypted-byok-key"):
            api_key, is_byok = await resolve_api_key(
                mock_db, "tenant-1", "apollo", skip_byok=False
            )

        assert api_key == "decrypted-byok-key"
        assert is_byok is True
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_byok_true_no_platform_key_raises(self):
        """When skip_byok=True and no platform key exists, ProviderError is raised."""
        from server.execution.service import resolve_api_key

        mock_db = AsyncMock()

        with patch("server.execution.service._PLATFORM_KEYS", {}):
            with pytest.raises(ProviderError, match="No API key found"):
                await resolve_api_key(
                    mock_db, "tenant-1", "some_provider", skip_byok=True
                )

    @pytest.mark.asyncio
    async def test_skip_byok_default_is_false(self):
        """Default skip_byok=False preserves existing behavior (checks BYOK first)."""
        from server.execution.service import resolve_api_key

        mock_byok = MagicMock()
        mock_byok.encrypted_key = "encrypted-byok-key"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_byok

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("server.execution.service.decrypt_key", return_value="decrypted-byok-key"):
            api_key, is_byok = await resolve_api_key(
                mock_db, "tenant-1", "apollo"
            )

        assert api_key == "decrypted-byok-key"
        assert is_byok is True
