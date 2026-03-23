"""Authentication commands: login, logout, status."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import click

from nrev_lite.client.auth import (
    clear_credentials,
    is_authenticated,
    load_credentials,
    save_credentials,
)
from nrev_lite.utils.config import get_api_base_url
from nrev_lite.utils.display import print_error, print_success, print_warning, spinner


# ---------------------------------------------------------------------------
# Localhost callback server for OAuth
# ---------------------------------------------------------------------------


class _OAuthCallbackResult:
    """Mutable container shared between the HTTP handler and the main thread."""

    def __init__(self) -> None:
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.user_info: dict[str, Any] | None = None
        self.expires_at: float | None = None
        self.error: str | None = None
        self.received = threading.Event()


def _make_handler(result: _OAuthCallbackResult):
    """Create a request-handler class that captures the callback."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            if "error" in params:
                result.error = params["error"][0]
                self._respond("Authentication failed. You can close this tab.")
                result.received.set()
                return

            result.access_token = params.get("access_token", [None])[0]
            result.refresh_token = params.get("refresh_token", [None])[0]
            expires_in = params.get("expires_in", [None])[0]
            if expires_in:
                result.expires_at = time.time() + float(expires_in)

            # user_info may arrive as JSON-encoded query param
            user_info_raw = params.get("user_info", [None])[0]
            if user_info_raw:
                try:
                    result.user_info = json.loads(user_info_raw)
                except json.JSONDecodeError:
                    result.user_info = {}

            # Also accept individual fields
            if not result.user_info:
                result.user_info = {}
            if "email" in params:
                result.user_info["email"] = params["email"][0]
            if "tenant" in params:
                result.user_info["tenant"] = params["tenant"][0]

            if result.access_token:
                self._respond(
                    "Authentication successful! You can close this tab and "
                    "return to your terminal."
                )
            else:
                result.error = "No access token received"
                self._respond("Authentication failed — no token received.")

            result.received.set()

        def _respond(self, body: str) -> None:
            html = (
                "<html><body style='font-family:sans-serif;text-align:center;"
                "padding-top:80px'>"
                f"<h2>{body}</h2></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Silence HTTP server logs
            pass

    return Handler


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _generate_pkce() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256).

    Returns (code_verifier, code_challenge).
    """
    code_verifier = secrets.token_urlsafe(64)  # 86 chars, well within 43-128
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


# ---------------------------------------------------------------------------
# Click commands
# ---------------------------------------------------------------------------


@click.group("auth")
def auth() -> None:
    """Manage authentication."""


@auth.command()
@click.option("--headless", is_flag=True, help="Use device-code flow (no browser).")
def login(headless: bool) -> None:
    """Log in to nrev-lite."""
    base_url = get_api_base_url()

    if headless:
        _device_code_flow(base_url)
    else:
        _browser_oauth_flow(base_url)


def _browser_oauth_flow(base_url: str) -> None:
    """Run the browser-based Google OAuth flow with a localhost callback."""
    import httpx

    port = _find_free_port()
    cli_redirect = f"http://localhost:{port}/callback"

    # Generate PKCE challenge pair
    code_verifier, code_challenge = _generate_pkce()

    result = _OAuthCallbackResult()
    handler_cls = _make_handler(result)
    server = HTTPServer(("127.0.0.1", port), handler_cls)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # Ask the nrev-lite server for the Google OAuth URL (with PKCE)
    try:
        resp = httpx.post(
            f"{base_url}/api/v1/auth/google",
            json={
                "redirect_uri": cli_redirect,
                "code_challenge": code_challenge,
                "code_verifier": code_verifier,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        oauth_url = data["auth_url"]
    except httpx.HTTPError as exc:
        print_error(f"Failed to initiate auth: {exc}")
        server.shutdown()
        sys.exit(1)

    click.echo("Opening browser for authentication...")
    click.echo(f"If the browser does not open, visit:\n  {oauth_url}\n")
    webbrowser.open(oauth_url)

    with spinner("Waiting for authentication..."):
        result.received.wait(timeout=120)

    server.shutdown()

    if not result.received.is_set():
        print_error("Timed out waiting for authentication callback.")
        sys.exit(1)

    if result.error:
        print_error(f"Authentication failed: {result.error}")
        sys.exit(1)

    if not result.access_token:
        print_error("No access token received from server.")
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


def _device_code_flow(base_url: str) -> None:
    """Run the headless device-code flow."""
    import httpx

    try:
        resp = httpx.post(f"{base_url}/api/v1/auth/device/code", timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        print_error(f"Failed to start device auth: {exc}")
        sys.exit(1)

    device_code = data["device_code"]
    verification_url = data["verification_uri"]
    user_code = data["user_code"]
    interval = data.get("interval", 5)

    click.echo(f"\nVisit:  {verification_url}")
    click.echo(f"Code:   {user_code}\n")

    with spinner("Waiting for you to authorize..."):
        deadline = time.time() + 300  # 5 minute timeout
        while time.time() < deadline:
            time.sleep(interval)
            try:
                poll_resp = httpx.post(
                    f"{base_url}/api/v1/auth/device/token",
                    json={"device_code": device_code},
                    timeout=15,
                )
                if poll_resp.status_code == 200:
                    token_data = poll_resp.json()
                    save_credentials(
                        access_token=token_data["access_token"],
                        refresh_token=token_data.get("refresh_token", ""),
                        user_info=token_data.get("user_info", {}),
                        expires_at=token_data.get(
                            "expires_at", time.time() + 3600
                        ),
                    )
                    email = token_data.get("user_info", {}).get("email", "unknown")
                    tenant = token_data.get("user_info", {}).get("tenant", "default")
                    print_success(f"Logged in as {email} (tenant: {tenant})")
                    return
                if poll_resp.status_code == 428:
                    # Authorization pending — keep polling
                    continue
                # Unexpected status
                print_error(
                    f"Unexpected response ({poll_resp.status_code}): "
                    f"{poll_resp.text}"
                )
                sys.exit(1)
            except httpx.HTTPError as exc:
                print_error(f"Polling error: {exc}")
                sys.exit(1)

    print_error("Timed out waiting for device authorization.")
    sys.exit(1)


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
