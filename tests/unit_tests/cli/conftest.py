"""Shared fixtures for CLI unit tests.

Centralises the "fake packaged assets" builder so every test can inject a
deterministic skills/rules tree without depending on the real packaged
wheel layout.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fake_packaged_assets(tmp_path: Path) -> tuple[Path, Path]:
    """Build a tmp 'claude_assets' tree matching the expected wheel layout.

    Returns (skills_root, rules_root) as real Paths that can stand in for
    the importlib.resources Traversable that _resolve_packaged_assets()
    would normally return.

    Layout:
        <tmp>/claude_assets/skills/apollo-enrichment/SKILL.md
        <tmp>/claude_assets/skills/rocketreach-enrichment/SKILL.md
        <tmp>/claude_assets/skills/provider-selection/SKILL.md
        <tmp>/claude_assets/rules/security.md
        <tmp>/claude_assets/rules/enrichment.md
    """
    assets = tmp_path / "claude_assets"
    skills = assets / "skills"
    rules = assets / "rules"

    for skill_name, body in [
        ("apollo-enrichment", "apollo skill body"),
        ("rocketreach-enrichment", "rocketreach skill body"),
        ("provider-selection", "provider-selection skill body"),
    ]:
        skill_dir = skills / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_name}\n---\n\n{body}\n"
        )

    rules.mkdir(parents=True)
    (rules / "security.md").write_text("# security rules\n")
    (rules / "enrichment.md").write_text("# enrichment rules\n")

    return skills, rules


@pytest.fixture
def patched_assets(monkeypatch: pytest.MonkeyPatch, fake_packaged_assets):
    """Redirect packaged-asset resolution to the fake tree.

    The install module uses ``importlib.resources.files(...)`` + ``as_file``
    directly, so we patch those on the module. ``as_file`` is replaced with
    a pass-through context manager that yields the real tmp Path, and
    ``files`` returns the assets root.

    Use this in tests that want the orchestrator to run end-to-end against
    deterministic source assets.
    """
    from contextlib import contextmanager

    from nrev_lite.cli import _claude_install

    skills, rules = fake_packaged_assets
    assets_root = skills.parent  # <tmp>/claude_assets

    real_files = _claude_install.resources.files
    real_as_file = _claude_install.resources.as_file

    def patched_files(name: str):
        if name == "nrev_lite.resources.claude_assets":
            return assets_root
        return real_files(name)

    @contextmanager
    def patched_as_file(traversable):
        if isinstance(traversable, Path):
            yield traversable
            return
        with real_as_file(traversable) as p:
            yield p

    monkeypatch.setattr(_claude_install.resources, "files", patched_files)
    monkeypatch.setattr(_claude_install.resources, "as_file", patched_as_file)
    return skills, rules


@pytest.fixture
def scope_dir(tmp_path: Path) -> Path:
    """Empty `.claude` scope directory — target for installs."""
    d = tmp_path / ".claude"
    d.mkdir()
    return d


@pytest.fixture
def claude_md_path(tmp_path: Path) -> Path:
    """Target CLAUDE.md path (does not yet exist)."""
    return tmp_path / "CLAUDE.md"
