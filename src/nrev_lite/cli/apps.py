"""App connection commands: list, available, connect, disconnect."""

from __future__ import annotations

import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import click

from nrev_lite.client.http import NrvApiError, NrvClient
from nrev_lite.utils.display import print_error, print_success, print_table, print_warning, spinner


def _require_auth() -> None:
    from nrev_lite.client.auth import is_authenticated

    if not is_authenticated():
        print_error("Not logged in. Run: nrev-lite auth login")
        sys.exit(1)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@click.group("apps")
def apps() -> None:
    """Manage connected apps (Gmail, Slack, HubSpot, etc.)."""


@apps.command("list")
def list_apps() -> None:
    """List connected apps."""
    _require_auth()

    client = NrvClient()
    try:
        with spinner("Fetching connected apps..."):
            result = client.app_list()
    except NrvApiError as exc:
        print_error(f"Failed: {exc.message}")
        sys.exit(1)

    connections = result.get("connections", [])
    active = [c for c in connections if (c.get("status") or "").upper() == "ACTIVE"]
    if not active:
        print_warning("No apps connected yet. Run: nrev-lite apps available")
        return

    columns = ["App", "Status", "Connected By", "Connected At"]
    rows = [
        [
            c.get("app_id", ""),
            c.get("status", "").lower(),
            c.get("connected_by", "—"),
            str(c.get("created_at", ""))[:19],
        ]
        for c in active
    ]
    print_table(columns, rows, title="Connected Apps")


@apps.command("available")
def available_apps() -> None:
    """Show all apps available for connection."""
    _require_auth()

    client = NrvClient()
    try:
        with spinner("Fetching available apps..."):
            result = client.app_available()
    except NrvApiError as exc:
        print_error(f"Failed: {exc.message}")
        sys.exit(1)

    apps_list = result.get("apps", [])
    if not apps_list:
        print_warning("No apps available.")
        return

    columns = ["App ID", "Name", "Category", "Connected"]
    rows = [
        [
            a.get("app_id", ""),
            f"{a.get('icon', '')} {a.get('name', '')}".strip(),
            a.get("category", ""),
            "✓" if a.get("connected") else "",
        ]
        for a in apps_list
    ]
    print_table(columns, rows, title="Available Apps")
    click.echo("\nConnect an app: nrev-lite apps connect <app_id>")


@apps.command("connect")
@click.argument("app_id")
def connect_app(app_id: str) -> None:
    """Connect an app via OAuth (opens browser).

    Examples:
        nrev-lite apps connect gmail
        nrev-lite apps connect slack
        nrev-lite apps connect hubspot
    """
    _require_auth()

    # Start a localhost callback server
    port = _find_free_port()
    redirect_uri = f"http://localhost:{port}/callback"

    client = NrvClient()

    # Initiate connection with CLI redirect
    try:
        with spinner(f"Initiating {app_id} connection..."):
            result = client.app_connect(app_id, redirect_uri=redirect_uri)
    except NrvApiError as exc:
        print_error(f"Failed to initiate connection: {exc.message}")
        sys.exit(1)

    if result.get("status") == "connected":
        # Some apps connect instantly without OAuth redirect
        print_success(result.get("message", f"{app_id} connected successfully!"))
        return

    redirect_url = result.get("redirect_url")
    if not redirect_url:
        print_error("Server did not return a redirect URL. Check the dashboard instead.")
        sys.exit(1)

    # Set up callback listener
    callback_result = _AppConnectResult()
    handler_cls = _make_app_connect_handler(callback_result)
    server = HTTPServer(("127.0.0.1", port), handler_cls)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    click.echo(f"\nOpening browser for {app_id} authorization...")
    click.echo(f"If the browser does not open, visit:\n  {redirect_url}\n")
    webbrowser.open(redirect_url)

    with spinner(f"Waiting for {app_id} authorization..."):
        callback_result.received.wait(timeout=120)

    server.shutdown()

    if not callback_result.received.is_set():
        print_warning("Timed out waiting for authorization. Check the dashboard to verify.")
        # Fall back to polling
        _poll_for_connection(client, app_id)
        return

    if callback_result.error:
        print_error(f"Connection failed: {callback_result.error}")
        sys.exit(1)

    print_success(f"{app_id} connected successfully!")


@apps.command("disconnect")
@click.argument("app_id")
def disconnect_app(app_id: str) -> None:
    """Disconnect an app.

    Examples:
        nrev-lite apps disconnect gmail
        nrev-lite apps disconnect slack
    """
    _require_auth()

    client = NrvClient()

    # Find the connection ID for this app
    try:
        with spinner("Finding connection..."):
            result = client.app_list()
    except NrvApiError as exc:
        print_error(f"Failed: {exc.message}")
        sys.exit(1)

    connections = result.get("connections", [])
    match = None
    for c in connections:
        if c.get("app_id") == app_id and (c.get("status") or "").upper() == "ACTIVE":
            match = c
            break

    if not match:
        print_warning(f"No active connection found for '{app_id}'.")
        return

    connection_id = match.get("id", "")
    if not connection_id:
        print_error("Connection has no ID — disconnect from the dashboard instead.")
        sys.exit(1)

    try:
        with spinner(f"Disconnecting {app_id}..."):
            client.app_disconnect(connection_id)
    except NrvApiError as exc:
        print_error(f"Failed: {exc.message}")
        sys.exit(1)

    print_success(f"{app_id} disconnected.")


# ---------------------------------------------------------------------------
# OAuth callback helpers
# ---------------------------------------------------------------------------


class _AppConnectResult:
    """Mutable container shared between the HTTP handler and the main thread."""

    def __init__(self) -> None:
        self.success: bool = False
        self.error: str | None = None
        self.received = threading.Event()


def _make_app_connect_handler(result: _AppConnectResult):
    """Create a request-handler class that captures the OAuth callback."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            status_param = params.get("status", ["success"])[0]

            if status_param in ("error", "failed"):
                result.error = params.get("message", ["Authorization failed"])[0]
                self._respond("Connection failed. You can close this tab.")
            else:
                result.success = True
                self._respond(
                    "App connected successfully! You can close this tab and "
                    "return to your terminal."
                )

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
            pass

    return Handler


def _poll_for_connection(client: NrvClient, app_id: str) -> None:
    """Fallback: poll the connections list to see if app connected."""
    import time

    click.echo("Polling for connection...")
    for _ in range(30):
        time.sleep(2)
        try:
            result = client.app_list()
            connections = result.get("connections", [])
            for c in connections:
                if c.get("app_id") == app_id and (c.get("status") or "").upper() == "ACTIVE":
                    print_success(f"{app_id} connected successfully!")
                    return
        except Exception:
            pass

    print_warning(f"Could not confirm {app_id} connection. Check the dashboard.")
