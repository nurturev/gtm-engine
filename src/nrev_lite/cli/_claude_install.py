"""Shared install module for Claude Code skills, rules, and CLAUDE.md.

Pure filesystem. No HTTP, no auth, no config lookups. Caller pre-resolves
console_url and scope paths. Consumed by `nrev-lite init` step 3 and
`nrev-lite setup-claude`.
"""

from __future__ import annotations

import re
import shutil
from importlib import resources
from pathlib import Path
from typing import Literal, NamedTuple

from nrev_lite.utils.display import print_warning

MANAGED_START = "<!-- nrev-lite:managed:start -->"
MANAGED_END = "<!-- nrev-lite:managed:end -->"
SKILL_PREFIX = "nrev-lite-"
RULES_SUBDIR = "nrev-lite"
LEGACY_STUB_NAME = "nrev-lite-gtm.md"

ClaudeMdAction = Literal["created", "replaced", "appended", "skipped"]


class InstallSummary(NamedTuple):
    skills_written: int
    rules_written: int
    claude_md_action: ClaudeMdAction


class ClaudeInstallError(Exception):
    """Fatal install error. Caller re-runs init to retry (idempotent)."""


def install_claude_assets(
    scope_dir: Path,
    claude_md_path: Path,
    console_url: str,
) -> InstallSummary:
    """Install packaged Claude Code assets under scope_dir.

    Writes skills under scope_dir/skills/nrev-lite-*, rules under
    scope_dir/rules/nrev-lite/, and refreshes the managed region of
    claude_md_path with console_url substituted.
    """
    if not console_url:
        raise ClaudeInstallError("console_url required")
    if scope_dir.name != ".claude":
        raise ClaudeInstallError(
            f"scope_dir must be a .claude directory, got: {scope_dir}"
        )

    skills_src, rules_src = _resolve_packaged_assets()

    try:
        scope_dir.mkdir(parents=True, exist_ok=True)
        skills_dir = scope_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        _sweep_nrev_skills(skills_dir)
        skills_written = _write_skills(skills_src, skills_dir)

        rules_written = _refresh_rules(rules_src, scope_dir / "rules" / RULES_SUBDIR)
    except OSError as exc:
        raise ClaudeInstallError(
            f"Filesystem write failed under {scope_dir}: {exc}"
        ) from exc

    claude_md_action = _refresh_claude_md(claude_md_path, console_url)

    try:
        _delete_legacy_stub(skills_dir)
    except OSError as exc:
        print_warning(f"Could not remove legacy stub: {exc}")

    return InstallSummary(skills_written, rules_written, claude_md_action)


def _resolve_packaged_assets() -> tuple[Path, Path]:
    """Return real filesystem paths to the packaged skills and rules trees.

    The package is shipped unpacked via hatchling `force-include`, so
    `resources.files()` yields a filesystem path we can pass directly to
    `shutil`. If the package is missing, raise a ClaudeInstallError with
    the actionable message.
    """
    try:
        root = resources.files("nrev_lite.resources.claude_assets")
    except (ModuleNotFoundError, FileNotFoundError) as exc:
        raise ClaudeInstallError(
            "Packaged claude_assets not found — reinstall pip package"
        ) from exc

    skills_src = Path(str(root / "skills"))
    rules_src = Path(str(root / "rules"))
    if not skills_src.is_dir() or not rules_src.is_dir():
        raise ClaudeInstallError(
            "Packaged claude_assets not found — reinstall pip package"
        )
    return skills_src, rules_src


def _sweep_nrev_skills(skills_dir: Path) -> int:
    if not skills_dir.is_dir():
        return 0
    count = 0
    for entry in skills_dir.iterdir():
        if entry.is_dir() and entry.name.startswith(SKILL_PREFIX):
            shutil.rmtree(entry)
            count += 1
    return count


def _write_skills(skills_src: Path, skills_dir: Path) -> int:
    count = 0
    for entry in sorted(skills_src.iterdir()):
        if not entry.is_dir():
            continue
        target = skills_dir / f"{SKILL_PREFIX}{entry.name}"
        shutil.copytree(entry, target)
        count += 1
    return count


def _refresh_rules(rules_src: Path, rules_target: Path) -> int:
    if rules_target.exists():
        shutil.rmtree(rules_target)
    rules_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(rules_src, rules_target)
    return sum(1 for _ in rules_target.rglob("*") if _.is_file())


def _refresh_claude_md(claude_md_path: Path, console_url: str) -> ClaudeMdAction:
    try:
        template = resources.files("nrev_lite.templates").joinpath(
            "user_claude.md"
        ).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise ClaudeInstallError(f"Template user_claude.md not found: {exc}") from exc

    substituted = template.replace("{{console_url}}", console_url)
    wrapped = f"{MANAGED_START}\n{substituted}\n{MANAGED_END}"

    try:
        claude_md_path.parent.mkdir(parents=True, exist_ok=True)

        if not claude_md_path.exists():
            claude_md_path.write_text(wrapped, encoding="utf-8")
            return "created"

        existing = claude_md_path.read_text(encoding="utf-8")
        starts = existing.count(MANAGED_START)
        ends = existing.count(MANAGED_END)

        if starts == 0 and ends == 0:
            claude_md_path.write_text(
                existing.rstrip() + "\n\n" + wrapped + "\n", encoding="utf-8"
            )
            return "appended"

        if (
            starts >= 1
            and ends >= 1
            and existing.index(MANAGED_START) < existing.index(MANAGED_END)
        ):
            if starts > 1 or ends > 1:
                print_warning(
                    "Multiple nrev-lite managed regions found in CLAUDE.md; "
                    "replacing the first occurrence"
                )
            pattern = re.compile(
                re.escape(MANAGED_START) + r".*?" + re.escape(MANAGED_END),
                re.DOTALL,
            )
            replaced = pattern.sub(lambda _m: wrapped, existing, count=1)
            claude_md_path.write_text(replaced, encoding="utf-8")
            return "replaced"

        print_warning(
            "CLAUDE.md managed-region markers are malformed; "
            "appending a fresh managed block"
        )
        claude_md_path.write_text(
            existing.rstrip() + "\n\n" + wrapped + "\n", encoding="utf-8"
        )
        return "appended"
    except OSError as exc:
        print_warning(f"Could not update CLAUDE.md at {claude_md_path}: {exc}")
        return "skipped"


def _delete_legacy_stub(skills_dir: Path) -> None:
    stub = skills_dir / LEGACY_STUB_NAME
    if stub.is_file():
        stub.unlink()
