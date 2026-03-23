"""Setup Claude Code integration: skills and CLAUDE.md."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from nrev_lite.utils.display import print_success, print_warning


def _load_claude_md() -> str:
    """Load the real CLAUDE.md content from the project root."""
    for candidate in [
        Path.cwd() / "CLAUDE.md",
        Path(__file__).resolve().parents[3] / "CLAUDE.md",
    ]:
        if candidate.exists():
            return candidate.read_text()
    # Fallback: minimal content
    return (
        "# nrev-lite \u2014 Agent-Native GTM Execution Platform\n\n"
        "Run `nrev-lite status` to see available commands and provider status.\n"
        "Run `nrev-lite init` for full setup.\n"
    )


NRV_SKILL = """\
---
name: nrev-lite-gtm
description: Interact with the nrev-lite GTM execution platform
---

Use the `nrev-lite` CLI to enrich, search, and query GTM data. Always ensure the
user is authenticated (`nrev-lite auth status`) before running commands.

Key patterns:
- Enrich a person: `nrev-lite enrich person --email X`
- Enrich a company: `nrev-lite enrich company --domain X`
- Search people: `nrev-lite search people --title X`
- Run SQL: `nrev-lite query "SELECT ..."`
"""


@click.command("setup-claude")
@click.option(
    "--project",
    is_flag=True,
    help="Install to project .claude/ instead of global ~/.claude/.",
)
def setup_claude(project: bool) -> None:
    """Install nrev-lite skills and CLAUDE.md for Claude Code."""
    if project:
        base = Path.cwd() / ".claude"
    else:
        base = Path.home() / ".claude"

    # Ensure directories exist
    skills_dir = base / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    # Write skill file
    skill_path = skills_dir / "nrev-lite-gtm.md"
    skill_path.write_text(NRV_SKILL)
    print_success(f"Skill installed: {skill_path}")

    # Write CLAUDE.md
    if project:
        claude_md_path = Path.cwd() / "CLAUDE.md"
    else:
        claude_md_path = base / "CLAUDE.md"

    claude_md_content = _load_claude_md()

    if claude_md_path.exists():
        print_warning(
            f"CLAUDE.md already exists at {claude_md_path}. "
            "Appending nrev-lite section."
        )
        existing = claude_md_path.read_text()
        if "nrev" not in existing.lower():
            claude_md_path.write_text(existing.rstrip() + "\n\n" + claude_md_content)
            print_success("nrev-lite section appended to CLAUDE.md.")
        else:
            print_warning("CLAUDE.md already contains nrev-lite content. Skipping.")
    else:
        claude_md_path.write_text(claude_md_content)
        print_success(f"CLAUDE.md created: {claude_md_path}")

    click.echo("\nClaude Code setup complete.")
