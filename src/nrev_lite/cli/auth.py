"""Authentication commands: login, logout, status."""

from __future__ import annotations

import json
import secrets
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import quote, urlparse

import click

from nrev_lite.client.auth import (
    clear_credentials,
    load_credentials,
    save_credentials,
)
from nrev_lite.utils.config import get_platform_base_url
from nrev_lite.utils.display import print_error, print_success, print_warning, spinner


# ---------------------------------------------------------------------------
# Localhost callback server (POST from platform's /cli/auth/done page)
# ---------------------------------------------------------------------------


class _OAuthCallbackResult:
    """Mutable container shared between the HTTP handler and the main thread."""

    def __init__(self, expected_state: str) -> None:
        self.expected_state = expected_state
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: float | None = None
        self.user_info: dict[str, Any] | None = None
        self.error: str | None = None
        self.received = threading.Event()


def _make_handler(result: _OAuthCallbackResult, allowed_origin: str):
    """Create a request-handler class that captures the POSTed credentials."""

    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self) -> None:  # noqa: N802
            # CORS preflight from the platform's /cli/auth/done page
            self.send_response(204)
            self._cors_headers()
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/callback":
                self.send_response(404)
                self._cors_headers()
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                self._reject(400, "invalid_json")
                return

            state = payload.get("state")
            if not state or state != result.expected_state:
                self._reject(400, "state_mismatch")
                return

            access_token = payload.get("access_token")
            if not access_token:
                self._reject(400, "missing_access_token")
                return

            result.access_token = access_token
            result.refresh_token = payload.get("refresh_token") or ""
            expires_in = payload.get("expires_in")
            if expires_in:
                try:
                    result.expires_at = time.time() + float(expires_in)
                except (TypeError, ValueError):
                    result.expires_at = None
            result.user_info = payload.get("user_info") or {}

            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            result.received.set()

        def _reject(self, status_code: int, reason: str) -> None:
            result.error = reason
            self.send_response(status_code)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": reason}).encode())
            result.received.set()

        def _cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers", "Content-Type"
            )
            self.send_header("Access-Control-Max-Age", "600")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Silence HTTP server logs
            pass

    return Handler


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _platform_origin(platform_base_url: str) -> str:
    """Return scheme://host[:port] for use as a CORS Allow-Origin value."""
    parsed = urlparse(platform_base_url)
    if not parsed.scheme or not parsed.netloc:
        return platform_base_url.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}"


def _build_login_url(
    platform_base_url: str, nonce: str, cli_callback: str
) -> str:
    """Construct the platform login URL with state + cli_callback nested
    inside the URL-encoded ``finalRedirect`` value.

    Shape::

        {platform}/login?finalRedirect=%2Fcli%2Fauth%2Fdone%3Fstate%3D{nonce}%26cli_callback%3D{enc}

    The nrev-ui-2 ``/login`` page reads the ``finalRedirect`` query param,
    completes Supabase auth, then navigates the browser to that path. The
    ``/cli/auth/done`` page in nrev-ui-2 then reads ``state`` and
    ``cli_callback`` from its own query string and POSTs the issued tokens
    to the CLI's localhost listener.
    """
    inner = (
        f"/cli/auth/done?state={quote(nonce, safe='')}"
        f"&cli_callback={quote(cli_callback, safe='')}"
    )
    return f"{platform_base_url.rstrip('/')}/login?finalRedirect={quote(inner, safe='')}"


# ---------------------------------------------------------------------------
# Click commands
# ---------------------------------------------------------------------------


@click.group("auth")
def auth() -> None:
    """Manage authentication."""


@auth.command()
def login() -> None:
    """Log in to nrev-lite via the platform's browser SSO flow."""
    _browser_oauth_flow(get_platform_base_url())


def _browser_oauth_flow(platform_base_url: str) -> None:
    """Open the platform login page and wait for a localhost POST callback.

    Concurrent ``nrev-lite auth login`` invocations are first-callback-wins:
    each call binds its own random localhost port, so they cannot collide
    on the listener; whichever browser tab completes first wins on the
    server side because the platform's /cli/auth/done POSTs to that
    invocation's specific localhost URL.
    """
    try:
        port = _find_free_port()
    except OSError as exc:
        print_error(
            f"Could not bind a localhost port for the auth callback: {exc}"
        )
        sys.exit(1)

    cli_callback = f"http://localhost:{port}/callback"
    nonce = secrets.token_urlsafe(32)

    result = _OAuthCallbackResult(expected_state=nonce)
    handler_cls = _make_handler(result, allowed_origin=_platform_origin(platform_base_url))
    server = HTTPServer(("127.0.0.1", port), handler_cls)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    login_url = _build_login_url(platform_base_url, nonce, cli_callback)

    click.echo("Opening browser for authentication...")
    click.echo(f"If the browser does not open, visit:\n  {login_url}\n")
    webbrowser.open(login_url)

    try:
        with spinner("Waiting for authentication..."):
            result.received.wait(timeout=300)
    finally:
        server.shutdown()

    if not result.received.is_set():
        print_error("Timed out waiting for authentication callback.")
        sys.exit(1)

    if result.error:
        print_error(f"Authentication failed: {result.error}")
        sys.exit(1)

    if not result.access_token:
        print_error("No access token received from platform.")
        sys.exit(1)

    save_credentials(
        access_token=result.access_token,
        refresh_token=result.refresh_token or "",
        user_info=result.user_info or {},
        expires_at=result.expires_at,
    )

    email = (result.user_info or {}).get("email", "unknown")
    tenant = (result.user_info or {}).get("tenant", "default")
    print_success(f"Logged in as {email} (tenant: {tenant})")


@auth.command()
def logout() -> None:
    """Log out and clear stored credentials."""
    clear_credentials()
    print_success("Logged out.")


@auth.command()
def status() -> None:
    """Show current authentication status."""
    creds = load_credentials()
    if creds is None:
        print_warning("Not logged in. Run: nrev-lite auth login")
        return

    user_info = creds.get("user_info", {})
    email = user_info.get("email", "unknown")
    tenant = user_info.get("tenant", "default")
    expires_at = creds.get("expires_at")

    click.echo(f"Email:   {email}")
    click.echo(f"Tenant:  {tenant}")

    if expires_at:
        remaining = expires_at - time.time()
        if remaining > 0:
            minutes = int(remaining // 60)
            click.echo(f"Token:   valid ({minutes}m remaining)")
        else:
            print_warning("Token:   expired (will refresh on next request)")
    else:
        click.echo("Token:   present")
