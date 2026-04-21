"""Setup Claude Code integration: thin wrapper over install_claude_assets.

Primary audience is Claude itself, when it detects skills missing
("run `nrev-lite setup-claude` in your terminal and restart this session").
Users go through `nrev-lite init` for the full flow.
"""

from __future__ import annotations

import sys

import click

from nrev_lite.cli._claude_install import ClaudeInstallError, install_claude_assets
from nrev_lite.cli._install_shared import resolve_console_url, resolve_scope_paths
from nrev_lite.utils.display import print_error, print_success, print_warning


@click.command("setup-claude")
@click.option(
    "--project",
    is_flag=True,
    help="Install to project .claude/ instead of global ~/.claude/.",
)
def setup_claude(project: bool) -> None:
    """Reinstall nrev-lite skills, rules, and CLAUDE.md.

    Diagnostic command for when Claude Code cannot see the shipped skills.
    Does NOT touch auth or MCP registration — those belong to `nrev-lite init`.
    """
    console_url = resolve_console_url()
    if not console_url:
        print_error(
            "Not authenticated. Run `nrev-lite auth login` first."
        )
        sys.exit(1)

    scope_dir, claude_md_path = resolve_scope_paths(project)

    try:
        summary = install_claude_assets(scope_dir, claude_md_path, console_url)
    except ClaudeInstallError as exc:
        print_error(f"Install failed: {exc}")
        sys.exit(1)

    if summary.claude_md_action == "skipped":
        print_warning(
            f"Installed {summary.skills_written} skills and "
            f"{summary.rules_written} rules, "
            f"but CLAUDE.md could not be updated. Re-run to retry."
        )
        sys.exit(2)

    print_success(
        f"Installed {summary.skills_written} skills and "
        f"{summary.rules_written} rules. "
        f"CLAUDE.md {summary.claude_md_action}."
    )
