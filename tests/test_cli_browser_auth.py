"""Unit tests for the CLI browser auth flow (GE-8).

Covers:
    * `_build_login_url` — finalRedirect URL shape (state + cli_callback nested)
    * `_platform_origin` — CORS Allow-Origin derivation
    * `get_platform_base_url` — config / env / default precedence
    * Localhost callback handler — POST happy path, state mismatch, OPTIONS preflight
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from http.server import HTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest

from nrev_lite.cli.auth import (
    _OAuthCallbackResult,
    _build_login_url,
    _make_handler,
    _platform_origin,
)
from nrev_lite.utils.config import (
    DEFAULT_PLATFORM_BASE_URL,
    get_platform_base_url,
)


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------


def test_build_login_url_nests_state_and_cli_callback_inside_finalRedirect():
    url = _build_login_url(
        "https://app.nrev.ai",
        "NONCE123",
        "http://localhost:54321/callback",
    )

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "app.nrev.ai"
    assert parsed.path == "/login"

    qs = parse_qs(parsed.query)
    # state and cli_callback must NOT appear at the top level
    assert "state" not in qs
    assert "cli_callback" not in qs

    # finalRedirect is single, URL-encoded value pointing at /cli/auth/done
    assert "finalRedirect" in qs
    inner = unquote(qs["finalRedirect"][0])
    assert inner.startswith("/cli/auth/done?")
    inner_parsed = urlparse(inner)
    inner_qs = parse_qs(inner_parsed.query)
    assert inner_qs["state"] == ["NONCE123"]
    assert inner_qs["cli_callback"] == ["http://localhost:54321/callback"]


def test_build_login_url_strips_trailing_slash_on_platform_base():
    url = _build_login_url(
        "https://app.nrev.ai/", "n", "http://localhost:1/callback"
    )
    assert url.startswith("https://app.nrev.ai/login?")


# ---------------------------------------------------------------------------
# CORS origin derivation
# ---------------------------------------------------------------------------


def test_platform_origin_strips_path_and_keeps_scheme_host_port():
    assert _platform_origin("https://app.nrev.ai/login?x=1") == "https://app.nrev.ai"
    assert _platform_origin("http://localhost:3000") == "http://localhost:3000"
    assert _platform_origin("https://staging.nrev.ai:8443/foo") == "https://staging.nrev.ai:8443"


# ---------------------------------------------------------------------------
# Platform base URL precedence
# ---------------------------------------------------------------------------


def test_get_platform_base_url_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("NREV_PLATFORM_URL", raising=False)
    with patch("nrev_lite.utils.config.get_config", return_value=None):
        assert get_platform_base_url() == DEFAULT_PLATFORM_BASE_URL


def test_get_platform_base_url_reads_env(monkeypatch):
    monkeypatch.setenv("NREV_PLATFORM_URL", "https://staging.nrev.ai/")
    with patch("nrev_lite.utils.config.get_config", return_value=None):
        assert get_platform_base_url() == "https://staging.nrev.ai"


def test_get_platform_base_url_config_takes_precedence_over_env(monkeypatch):
    monkeypatch.setenv("NREV_PLATFORM_URL", "https://staging.nrev.ai")
    with patch(
        "nrev_lite.utils.config.get_config",
        return_value="https://app.local.test/",
    ):
        assert get_platform_base_url() == "https://app.local.test"


# ---------------------------------------------------------------------------
# Localhost callback handler — full HTTP server round trip
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def callback_server():
    """Spin up the real localhost listener used by the CLI auth flow."""
    port = _free_port()
    result = _OAuthCallbackResult(expected_state="NONCE-EXPECTED")
    handler_cls = _make_handler(result, allowed_origin="https://app.nrev.ai")
    server = HTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port, result
    finally:
        server.shutdown()


def test_callback_accepts_valid_post_and_records_tokens(callback_server):
    port, result = callback_server

    resp = httpx.post(
        f"http://127.0.0.1:{port}/callback",
        json={
            "state": "NONCE-EXPECTED",
            "access_token": "ey.access",
            "refresh_token": "rT_value",
            "expires_in": 3600,
            "user_info": {"email": "u@x.com", "tenant": "t1"},
        },
        headers={"Origin": "https://app.nrev.ai"},
        timeout=5,
    )

    assert resp.status_code == 200
    assert resp.headers.get("Access-Control-Allow-Origin") == "https://app.nrev.ai"
    assert json.loads(resp.text) == {"ok": True}

    assert result.received.is_set()
    assert result.access_token == "ey.access"
    assert result.refresh_token == "rT_value"
    assert result.user_info == {"email": "u@x.com", "tenant": "t1"}
    assert result.expires_at is not None
    assert result.expires_at > time.time()


def test_callback_rejects_state_mismatch(callback_server):
    port, result = callback_server

    resp = httpx.post(
        f"http://127.0.0.1:{port}/callback",
        json={"state": "WRONG", "access_token": "ey"},
        timeout=5,
    )

    assert resp.status_code == 400
    body = json.loads(resp.text)
    assert body["ok"] is False
    assert body["error"] == "state_mismatch"
    assert result.access_token is None
    assert result.error == "state_mismatch"
    assert result.received.is_set()  # so the main thread unblocks and reports


def test_callback_rejects_missing_access_token(callback_server):
    port, result = callback_server

    resp = httpx.post(
        f"http://127.0.0.1:{port}/callback",
        json={"state": "NONCE-EXPECTED"},
        timeout=5,
    )

    assert resp.status_code == 400
    assert json.loads(resp.text)["error"] == "missing_access_token"
    assert result.access_token is None


def test_callback_handles_options_preflight(callback_server):
    port, _ = callback_server

    resp = httpx.request(
        "OPTIONS",
        f"http://127.0.0.1:{port}/callback",
        headers={
            "Origin": "https://app.nrev.ai",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
        timeout=5,
    )

    assert resp.status_code == 204
    assert resp.headers.get("Access-Control-Allow-Origin") == "https://app.nrev.ai"
    assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")
    assert "Content-Type" in resp.headers.get("Access-Control-Allow-Headers", "")
