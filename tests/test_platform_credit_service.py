"""Unit tests for server/billing/platform_credit_service.py"""

from __future__ import annotations

import pytest
import httpx

from unittest.mock import patch, AsyncMock, MagicMock


MOCK_SETTINGS = {
    "PLATFORM_CREDIT_SERVICE_URL": "http://um-service:8000/private",
    "PLATFORM_CREDIT_SERVICE_TOKEN": "test-token",
}


@pytest.fixture(autouse=True)
def mock_settings():
    """Patch settings for all tests."""
    with patch("server.billing.platform_credit_service.settings") as mock:
        mock.PLATFORM_CREDIT_SERVICE_URL = MOCK_SETTINGS["PLATFORM_CREDIT_SERVICE_URL"]
        mock.PLATFORM_CREDIT_SERVICE_TOKEN = MOCK_SETTINGS["PLATFORM_CREDIT_SERVICE_TOKEN"]
        yield mock


# ---------------------------------------------------------------------------
# _validate_tenant_id
# ---------------------------------------------------------------------------


class TestValidateTenantId:
    def test_numeric_string(self):
        from server.billing.platform_credit_service import _validate_tenant_id

        assert _validate_tenant_id("123") == 123

    def test_non_numeric_string(self):
        from server.billing.platform_credit_service import _validate_tenant_id

        assert _validate_tenant_id("tn_abc123") is None

    def test_none_input(self):
        from server.billing.platform_credit_service import _validate_tenant_id

        assert _validate_tenant_id(None) is None

    def test_empty_string(self):
        from server.billing.platform_credit_service import _validate_tenant_id

        assert _validate_tenant_id("") is None


# ---------------------------------------------------------------------------
# check_platform_credits
# ---------------------------------------------------------------------------


class TestCheckPlatformCredits:
    @pytest.mark.asyncio
    async def test_returns_balance(self):
        from server.billing.platform_credit_service import check_platform_credits

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = 42.5
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.billing.platform_credit_service.httpx.AsyncClient", return_value=mock_client):
            result = await check_platform_credits("123")

        assert result == 42.5
        mock_client.get.assert_called_once()
        call_kwargs = mock_client.get.call_args
        assert call_kwargs.kwargs["params"]["tenant_id"] == 123

    @pytest.mark.asyncio
    async def test_returns_402_insufficient(self):
        from server.billing.platform_credit_service import check_platform_credits

        mock_response = MagicMock()
        mock_response.status_code = 402

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.billing.platform_credit_service.httpx.AsyncClient", return_value=mock_client):
            result = await check_platform_credits("123")

        assert result == 0.0

    @pytest.mark.asyncio
    async def test_platform_unreachable_fail_closed(self):
        from server.billing.platform_credit_service import check_platform_credits

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.billing.platform_credit_service.httpx.AsyncClient", return_value=mock_client):
            result = await check_platform_credits("123")

        assert result == 0.0

    @pytest.mark.asyncio
    async def test_non_numeric_tenant_returns_zero(self):
        from server.billing.platform_credit_service import check_platform_credits

        result = await check_platform_credits("tn_abc123")
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_with_required_amount_sends_credit_count(self):
        from server.billing.platform_credit_service import check_platform_credits

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = 50.0
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.billing.platform_credit_service.httpx.AsyncClient", return_value=mock_client):
            result = await check_platform_credits("123", required_amount=10)

        assert result == 50.0
        call_kwargs = mock_client.get.call_args
        assert call_kwargs.kwargs["params"]["credit_count"] == 10

    @pytest.mark.asyncio
    async def test_without_required_amount_no_credit_count_param(self):
        from server.billing.platform_credit_service import check_platform_credits

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = 50.0
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.billing.platform_credit_service.httpx.AsyncClient", return_value=mock_client):
            await check_platform_credits("123")

        call_kwargs = mock_client.get.call_args
        assert "credit_count" not in call_kwargs.kwargs["params"]

    @pytest.mark.asyncio
    async def test_null_balance_returns_zero(self):
        from server.billing.platform_credit_service import check_platform_credits

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = None
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.billing.platform_credit_service.httpx.AsyncClient", return_value=mock_client):
            result = await check_platform_credits("123")

        assert result == 0.0


# ---------------------------------------------------------------------------
# debit_platform_credits
# ---------------------------------------------------------------------------


class TestDebitPlatformCredits:
    @pytest.mark.asyncio
    async def test_successful_debit(self):
        from server.billing.platform_credit_service import debit_platform_credits

        mock_response = MagicMock()
        mock_response.status_code = 202

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.billing.platform_credit_service.httpx.AsyncClient", return_value=mock_client):
            await debit_platform_credits("123", 5, "search_people")

        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_name_in_body(self):
        from server.billing.platform_credit_service import debit_platform_credits

        mock_response = MagicMock()
        mock_response.status_code = 202

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.billing.platform_credit_service.httpx.AsyncClient", return_value=mock_client):
            await debit_platform_credits("123", 5, "enrich_person", agent_thread_id="wf-1")

        call_kwargs = mock_client.post.call_args
        body = call_kwargs.kwargs["json"]
        assert body["event_name"] == "enrich_person"
        assert body["agent_thread_id"] == "wf-1"

    @pytest.mark.asyncio
    async def test_tenant_id_cast_to_int_in_body(self):
        from server.billing.platform_credit_service import debit_platform_credits

        mock_response = MagicMock()
        mock_response.status_code = 202

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.billing.platform_credit_service.httpx.AsyncClient", return_value=mock_client):
            await debit_platform_credits("456", 3, "google_search")

        call_kwargs = mock_client.post.call_args
        body = call_kwargs.kwargs["json"]
        assert body["tenant_id"] == 456
        assert isinstance(body["tenant_id"], int)

    @pytest.mark.asyncio
    async def test_returns_400_logs_warning(self):
        from server.billing.platform_credit_service import debit_platform_credits

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request: missing field"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.billing.platform_credit_service.httpx.AsyncClient", return_value=mock_client):
            # Should not raise
            await debit_platform_credits("123", 1, "scrape_page")

    @pytest.mark.asyncio
    async def test_platform_unreachable_no_raise(self):
        from server.billing.platform_credit_service import debit_platform_credits

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.billing.platform_credit_service.httpx.AsyncClient", return_value=mock_client):
            # Should not raise
            await debit_platform_credits("123", 2, "enrich_company")

    @pytest.mark.asyncio
    async def test_non_numeric_tenant_skips_debit(self):
        from server.billing.platform_credit_service import debit_platform_credits

        with patch("server.billing.platform_credit_service.httpx.AsyncClient") as mock_cls:
            await debit_platform_credits("tn_abc123", 1, "search_people")
            # AsyncClient should never be instantiated
            mock_cls.assert_not_called()
