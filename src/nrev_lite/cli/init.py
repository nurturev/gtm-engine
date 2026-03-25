"""nrev-lite init — one-command onboarding for new users.

Handles the complete setup flow:
1. Authenticate (Google OAuth via browser)
2. Register the MCP server via `claude mcp add`
3. Verify everything works

After `nrev-lite init`, every new Claude Code session automatically has access
to all 33 nrev-lite tools.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import click

from nrev_lite.client.auth import is_authenticated, load_credentials
from nrev_lite.utils.config import get_api_base_url
from nrev_lite.utils.display import print_error, print_success, print_warning


def _find_nrev_executable() -> str:
    """Find the path to the nrev-lite entry point for MCP server.

    Returns the absolute path to the nrev-lite binary that Claude Code
    should use to start the MCP server. Falls back to python -m.
    """
    nrev_bin = shutil.which("nrev-lite")
    if nrev_bin:
        return nrev_bin

    python_bin = shutil.which("python3") or shutil.which("python") or sys.executable
    return python_bin


def _is_already_registered() -> bool:
    """Check if nrev-lite MCP server is already registered in Claude Code."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return False

    result = subprocess.run(
        ["claude", "mcp", "list"],
        capture_output=True,
        text=True
    )
    return "nrev-lite" in result.stdout


def _register_mcp_server(scope: str) -> bool:
    """Register nrev-lite as an MCP server via `claude mcp add`.

    Uses the Claude Code CLI to register the server in the correct
    config file (~/.claude.json), which is the only file Claude Code
    reads MCP server definitions from.

    Args:
        scope: "user" for all sessions, "local" for current project only.

    Returns True if registration was successful.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        print_error(
            "Claude Code CLI not found on PATH.\n"
            "  Install it from: https://claude.ai/download\n"
            "  Then run `nrev-lite init` again."
        )
        return False

    nrev_bin = _find_nrev_executable()

    # Build the command for `claude mcp add`
    if nrev_bin.endswith("nrev-lite"):
        cmd = [
            "claude", "mcp", "add",
            "-s", scope,
            "nrev-lite",
            "--",
            nrev_bin, "mcp", "serve"
        ]
    else:
        cmd = [
            "claude", "mcp", "add",
            "-s", scope,
            "nrev-lite",
            "--",
            nrev_bin, "-m", "nrev_lite.mcp.server"
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        print_error(f"Failed to register MCP server: {stderr}")
        return False

    return True


def _unregister_mcp_server(scope: str) -> None:
    """Remove existing nrev-lite MCP registration (for re-registration)."""
    subprocess.run(
        ["claude", "mcp", "remove", "-s", scope, "nrev-lite"],
        capture_output=True,
        text=True
    )


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
    help="Register MCP server for this project only."
)
@click.option(
    "--skip-auth",
    is_flag=True,
    help="Skip authentication (if already logged in)."
)
@click.option(
    "--server-url",
    default=None,
    help="nrev-lite server URL (default: http://localhost:8000 or configured value)."
)
@click.option(
    "--force",
    is_flag=True,
    help="Re-register MCP server even if already registered."
)
def init(project: bool, skip_auth: bool, server_url: str | None, force: bool) -> None:
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
        nrev-lite init --force            # Re-register even if already set up
        nrev-lite init --server-url https://api.nrev.dev
    """
    click.echo()
    click.secho("  nrev-lite — Agent-Native GTM Platform", fg="cyan", bold=True)
    click.secho("  ─────────────────────────────────", fg="cyan")
    click.echo()

    # ── Pre-check: Claude Code CLI must be available ──────────────────
    if not shutil.which("claude"):
        print_error(
            "Claude Code CLI not found on PATH.\n"
            "  Install it from: https://claude.ai/download\n"
            "  Then run `nrev-lite init` again."
        )
        sys.exit(1)

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

    # ── Step 2: Register MCP server via `claude mcp add` ─────────────
    scope = "local" if project else "user"
    scope_label = "this project" if project else "all Claude Code sessions"

    click.secho("  Step 2/3 — Register MCP Server", bold=True)
    click.echo(f"  Scope: {scope_label}")

    if _is_already_registered() and not force:
        print_success("nrev-lite MCP server already registered")
    else:
        if force and _is_already_registered():
            click.echo("  Re-registering (--force)...")
            _unregister_mcp_server(scope)

        if _register_mcp_server(scope):
            print_success("MCP server registered via `claude mcp add`")
        else:
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
    click.echo("    nrev-lite credits balance  Check your credit balance")
    click.echo("    nrev-lite dashboard       Open the web dashboard")
    click.echo()
