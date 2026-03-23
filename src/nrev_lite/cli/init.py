"""nrev-lite init — one-command onboarding for new users.

Handles the complete setup flow:
1. Authenticate (Google OAuth via browser)
2. Register the MCP server in Claude Code's settings
3. Verify everything works

After `nrev-lite init`, every new Claude Code session automatically has access
to all 22 nrev-lite tools.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import click

from nrev_lite.client.auth import is_authenticated, load_credentials
from nrev_lite.utils.config import get_api_base_url
from nrev_lite.utils.display import print_error, print_success, print_warning


# ---------------------------------------------------------------------------
# Claude Code settings paths
# ---------------------------------------------------------------------------

# Global settings — tools available in ALL Claude Code sessions
_CLAUDE_GLOBAL_SETTINGS = Path.home() / ".claude" / "settings.json"

# Project-level settings — tools only available in this project
_CLAUDE_PROJECT_SETTINGS = Path.cwd() / ".mcp.json"


def _find_nrev_executable() -> str:
    """Find the path to the nrev-lite entry point for MCP server.

    Returns the command that Claude Code should use to start the MCP server.
    Prefers `nrev-lite` CLI if available on PATH, falls back to `python3 -m`.
    """
    # Check if `nrev-lite` is on PATH
    nrev_bin = shutil.which("nrev-lite")
    if nrev_bin:
        return nrev_bin

    # Check if the current python has nrev-lite installed
    python_bin = shutil.which("python3") or shutil.which("python") or sys.executable
    return python_bin


def _build_mcp_config() -> dict[str, Any]:
    """Build the MCP server configuration for Claude Code."""
    nrev_bin = _find_nrev_executable()

    # If we found the `nrev-lite` binary, use it directly
    if nrev_bin.endswith("nrev-lite"):
        return {
            "command": nrev_bin,
            "args": ["mcp", "serve"],
        }

    # Otherwise use python -m
    return {
        "command": nrev_bin,
        "args": ["-m", "nrev_lite.mcp.server"],
    }


def _read_json_file(path: Path) -> dict[str, Any]:
    """Safely read a JSON file, returning empty dict on failure."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json_file(path: Path, data: dict[str, Any]) -> None:
    """Write data as formatted JSON, creating parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _register_mcp_server(scope: str) -> bool:
    """Register nrev-lite as an MCP server in Claude Code settings.

    Args:
        scope: "global" for ~/.claude/settings.json, "project" for .mcp.json

    Returns True if registration was successful.
    """
    if scope == "project":
        settings_path = _CLAUDE_PROJECT_SETTINGS
    else:
        settings_path = _CLAUDE_GLOBAL_SETTINGS

    settings = _read_json_file(settings_path)

    # Check if already registered
    mcp_servers = settings.get("mcpServers", {})
    if "nrev-lite" in mcp_servers:
        click.echo(f"  nrev-lite MCP server already registered in {settings_path}")
        return True

    # Add the nrev-lite server
    mcp_config = _build_mcp_config()

    if "mcpServers" not in settings:
        settings["mcpServers"] = {}

    settings["mcpServers"]["nrev-lite"] = mcp_config

    _write_json_file(settings_path, settings)
    return True


def _verify_server_reachable() -> bool:
    """Check if the nrev-lite API server is reachable."""
    import httpx

    base_url = get_api_base_url()
    try:
        resp = httpx.get(f"{base_url}/health", timeout=5)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


@click.command("init")
@click.option(
    "--project",
    is_flag=True,
    help="Register MCP server for this project only (creates .mcp.json).",
)
@click.option(
    "--skip-auth",
    is_flag=True,
    help="Skip authentication (if already logged in).",
)
@click.option(
    "--server-url",
    default=None,
    help="nrev-lite server URL (default: http://localhost:8000 or configured value).",
)
def init(project: bool, skip_auth: bool, server_url: str | None) -> None:
    """Set up nrev-lite for Claude Code in one command.

    \b
    This command:
      1. Authenticates you via Google (opens browser)
      2. Registers the nrev-lite MCP server with Claude Code
      3. Verifies the connection works

    \b
    After running this, every new Claude Code session will have access to
    all nrev-lite tools — search, enrichment, connections, and more.

    \b
    Examples:
        nrev-lite init                    # Full setup (global)
        nrev-lite init --project          # Project-level only
        nrev-lite init --skip-auth        # Already logged in, just register MCP
        nrev-lite init --server-url https://api.nrev.dev
    """
    click.echo()
    click.secho("  nrev-lite — Agent-Native GTM Platform", fg="cyan", bold=True)
    click.secho("  ─────────────────────────────────", fg="cyan")
    click.echo()

    # ── Step 0: Configure server URL if provided ──────────────────────
    if server_url:
        from nrev_lite.utils.config import set_config
        set_config("server.url", server_url.rstrip("/"))
        click.echo(f"  Server URL set to: {server_url}")
        click.echo()

    # ── Step 1: Authentication ────────────────────────────────────────
    click.secho("  Step 1/3 — Authentication", bold=True)

    if skip_auth and is_authenticated():
        creds = load_credentials()
        email = (creds or {}).get("user_info", {}).get("email", "unknown")
        click.echo(f"  Already logged in as {email}")
    elif is_authenticated():
        creds = load_credentials()
        email = (creds or {}).get("user_info", {}).get("email", "unknown")
        click.echo(f"  Already logged in as {email}")

        if not click.confirm("  Use existing session?", default=True):
            click.echo("  Opening browser for authentication...")
            from nrev_lite.cli.auth import _browser_oauth_flow
            _browser_oauth_flow(get_api_base_url())
    else:
        click.echo("  Opening browser for Google authentication...")
        click.echo()
        from nrev_lite.cli.auth import _browser_oauth_flow
        _browser_oauth_flow(get_api_base_url())

    # Verify auth succeeded
    if not is_authenticated():
        print_error("Authentication failed. Run `nrev-lite auth login` manually.")
        sys.exit(1)

    creds = load_credentials()
    email = (creds or {}).get("user_info", {}).get("email", "unknown")
    tenant = (creds or {}).get("user_info", {}).get("tenant", "unknown")
    print_success(f"Authenticated as {email} (tenant: {tenant})")
    click.echo()

    # ── Step 2: Register MCP server ───────────────────────────────────
    scope = "project" if project else "global"
    scope_label = "this project" if project else "all Claude Code sessions"
    settings_path = _CLAUDE_PROJECT_SETTINGS if project else _CLAUDE_GLOBAL_SETTINGS

    click.secho("  Step 2/3 — Register MCP Server", bold=True)
    click.echo(f"  Scope: {scope_label}")

    if _register_mcp_server(scope):
        print_success(f"MCP server registered in {settings_path}")
    else:
        print_error("Failed to register MCP server.")
        sys.exit(1)

    click.echo()

    # ── Step 3: Verify connection ─────────────────────────────────────
    click.secho("  Step 3/3 — Verify Connection", bold=True)

    if _verify_server_reachable():
        print_success("Server is reachable")
    else:
        base_url = get_api_base_url()
        print_warning(
            f"Server at {base_url} is not reachable right now.\n"
            "  That's OK — the MCP server will connect when the API is running."
        )

    # ── Done ──────────────────────────────────────────────────────────
    click.echo()
    click.secho("  ─────────────────────────────────", fg="green")
    click.secho("  Setup complete!", fg="green", bold=True)
    click.echo()
    click.echo("  What happens now:")
    click.echo("  • Open a new Claude Code session (or restart the current one)")
    click.echo("  • Claude will automatically have access to all nrev-lite tools")
    click.echo("  • Try asking: \"Search for Series B SaaS companies hiring VPs of Sales\"")
    click.echo()
    click.echo("  Useful commands:")
    click.echo("    nrev-lite status          Show auth & connection status")
    click.echo("    nrev-lite credits balance Check your credit balance")
    click.echo("    nrev-lite dashboard       Open the web dashboard")
    click.echo()
