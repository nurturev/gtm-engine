"""Shared helpers for the Claude install flow, used by both `init` and `setup-claude`."""

from __future__ import annotations

from pathlib import Path

from nrev_lite.client.auth import load_credentials
from nrev_lite.utils.config import get_api_base_url


def resolve_console_url() -> str | None:
    """Resolve the user's dashboard URL from local auth + configured server URL.

    Returns None when credentials are missing or malformed so the caller can
    surface a clean `run nrev-lite auth login first` error.
    """
    creds = load_credentials()
    if not creds:
        return None
    tenant_id = (creds.get("user_info") or {}).get("tenant")
    if not tenant_id:
        return None
    base_url = get_api_base_url().rstrip("/")
    return f"{base_url}/console/{tenant_id}"


def resolve_scope_paths(project: bool) -> tuple[Path, Path]:
    """Return (scope_dir, claude_md_path) based on the --project flag.

    Global: ~/.claude/ and ~/.claude/CLAUDE.md.
    Project: <cwd>/.claude/ and <cwd>/CLAUDE.md.
    """
    if project:
        scope_dir = Path.cwd() / ".claude"
        claude_md_path = Path.cwd() / "CLAUDE.md"
    else:
        scope_dir = Path.home() / ".claude"
        claude_md_path = Path.home() / ".claude" / "CLAUDE.md"
    return scope_dir, claude_md_path
