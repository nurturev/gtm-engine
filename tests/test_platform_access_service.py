"""Unit tests for server/auth/platform_access_service.py"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


MOCK_URL = "http://um-service:8000/private"
MOCK_TOKEN = "test-token"


@pytest.fixture(autouse=True)
def mock_settings():
    with patch("server.auth.platform_access_service.settings") as mock:
        mock.PLATFORM_CREDIT_SERVICE_URL = MOCK_URL
        mock.PLATFORM_CREDIT_SERVICE_TOKEN = MOCK_TOKEN
        yield mock


@pytest.fixture
def mock_redis_miss():
    """Redis that returns None on get (cache miss) and accepts set."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    with patch(
        "server.auth.platform_access_service._get_redis", return_value=redis
    ):
        yield redis


def _mock_httpx(response_json: dict | None = None, status_code: int = 200,
                side_effect=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = response_json or {}
    resp.raise_for_status = MagicMock()

    client = AsyncMock()
    if side_effect is not None:
        client.get = AsyncMock(side_effect=side_effect)
    else:
        client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


class TestHasTenantAccess:
    @pytest.mark.asyncio
    async def test_um_true(self, mock_redis_miss):
        from server.auth.platform_access_service import has_tenant_access

        client = _mock_httpx({"has_access": True})
        with patch(
            "server.auth.platform_access_service.httpx.AsyncClient",
            return_value=client,
        ):
            assert await has_tenant_access("a@b.com", "3") is True
        mock_redis_miss.set.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_um_false(self, mock_redis_miss):
        from server.auth.platform_access_service import has_tenant_access

        client = _mock_httpx({"has_access": False})
        with patch(
            "server.auth.platform_access_service.httpx.AsyncClient",
            return_value=client,
        ):
            assert await has_tenant_access("a@b.com", "3") is False

    @pytest.mark.asyncio
    async def test_um_timeout_fails_closed(self, mock_redis_miss):
        from server.auth.platform_access_service import has_tenant_access

        client = _mock_httpx(side_effect=httpx.ConnectError("boom"))
        with patch(
            "server.auth.platform_access_service.httpx.AsyncClient",
            return_value=client,
        ):
            assert await has_tenant_access("a@b.com", "3") is False

    @pytest.mark.asyncio
    async def test_um_404_returns_false(self, mock_redis_miss):
        from server.auth.platform_access_service import has_tenant_access

        client = _mock_httpx(status_code=404)
        with patch(
            "server.auth.platform_access_service.httpx.AsyncClient",
            return_value=client,
        ):
            assert await has_tenant_access("unknown@b.com", "3") is False

    @pytest.mark.asyncio
    async def test_cache_hit_skips_http(self):
        from server.auth.platform_access_service import has_tenant_access

        redis = AsyncMock()
        redis.get = AsyncMock(return_value="1")
        redis.set = AsyncMock()

        with patch(
            "server.auth.platform_access_service._get_redis", return_value=redis
        ), patch(
            "server.auth.platform_access_service.httpx.AsyncClient"
        ) as client_cls:
            assert await has_tenant_access("a@b.com", "3") is True
            client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_inputs_reject(self):
        from server.auth.platform_access_service import has_tenant_access

        assert await has_tenant_access("", "3") is False
        assert await has_tenant_access("a@b.com", "") is False

    @pytest.mark.asyncio
    async def test_no_um_url_denies(self, mock_redis_miss):
        from server.auth.platform_access_service import has_tenant_access

        with patch(
            "server.auth.platform_access_service.settings"
        ) as s:
            s.PLATFORM_CREDIT_SERVICE_URL = None
            s.PLATFORM_CREDIT_SERVICE_TOKEN = None
            assert await has_tenant_access("a@b.com", "3") is False
