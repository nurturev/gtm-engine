"""API tests for `nrev-lite init` Step 3 and `nrev-lite setup-claude`.

These are the "API boundary" of the skill-distribution feature: the CLI
commands that users and Claude invoke. No HTTP, no DB — the boundary is
the Click command + the filesystem it mutates.

Scenarios (per the API testing blueprint §3 — scenario list):

    install_claude_assets end-to-end (library entry):
      - fresh scope installs every packaged skill + rule + CLAUDE.md → Complete
      - seeded legacy stub is removed on install → regression
      - seeded stale `nrev-lite-<old-name>` skill is swept on reinstall
      - seeded user-authored skill is preserved on reinstall
      - seeded legacy CLAUDE.md without sentinel markers → new block appended,
        old block remains (HLD §11 accepted artifact)

    nrev-lite setup-claude (Click CLI):
      - auth missing → errors out with "nrev-lite auth login" hint, nothing written
      - auth present → delegates to install_claude_assets, reports summary
      - --project flag installs into <cwd>/.claude

    nrev-lite init Step 3 (Click CLI, other steps mocked):
      - successful install → "Complete" three-state outcome, exit 0
      - CLAUDE.md write fails → "Partial" outcome, non-zero exit, skills installed
      - install_claude_assets raises ClaudeInstallError → "Failure" outcome,
        non-zero exit, error includes retry hint

Test isolation (blueprint §6):
    - tmp_path as HOME / cwd for each test — no shared filesystem
    - monkeypatch the packaged-assets resolver to inject a deterministic tree
    - monkeypatch auth + MCP steps in init — we test Step 3, not auth
    - Click's CliRunner gives us the full command stack including argument
      parsing, exit codes, stdout/stderr
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from nrev_lite.cli import _claude_install
from nrev_lite.cli._claude_install import (
    ClaudeInstallError,
    install_claude_assets,
)

MARKER_START = "<!-- nrev-lite:managed:start -->"
MARKER_END = "<!-- nrev-lite:managed:end -->"

CONSOLE_URL = "https://api.nrev.ai/console/tenant-42"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def fake_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build + inject a deterministic packaged tree for every test."""
    assets = tmp_path / "pkg" / "claude_assets"
    skills = assets / "skills"
    rules = assets / "rules"

    for name in ["apollo-enrichment", "provider-selection", "list-building"]:
        d = skills / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\n---\n\n{name} body\n"
        )

    rules.mkdir(parents=True)
    (rules / "security.md").write_text("# security\n")
    (rules / "enrichment.md").write_text("# enrichment\n")

    monkeypatch.setattr(
        _claude_install,
        "_resolve_packaged_assets",
        lambda: (skills, rules),
    )
    return skills, rules


@pytest.fixture
def scope(tmp_path: Path) -> Path:
    d = tmp_path / ".claude"
    d.mkdir()
    return d


@pytest.fixture
def claude_md(tmp_path: Path) -> Path:
    return tmp_path / "CLAUDE.md"


# =============================================================================
# install_claude_assets — end-to-end scenarios
# =============================================================================


class TestInstallEndToEnd:
    def test_fresh_scope_installs_every_packaged_skill_and_rule(
        self, scope: Path, claude_md: Path, fake_assets
    ):
        # Given — empty .claude/ scope, no CLAUDE.md

        # When — run the install function end-to-end
        summary = install_claude_assets(scope, claude_md, CONSOLE_URL)

        # Then — final layout matches the packaged layout
        assert summary.skills_written == 3
        assert summary.rules_written == 2
        assert summary.claude_md_action == "created"

        expected_skills = {
            "nrev-lite-apollo-enrichment",
            "nrev-lite-provider-selection",
            "nrev-lite-list-building",
        }
        actual_skills = {p.name for p in (scope / "skills").iterdir()}
        assert actual_skills == expected_skills

        expected_rules = {"security.md", "enrichment.md"}
        actual_rules = {
            p.name for p in (scope / "rules" / "nrev-lite").iterdir()
        }
        assert actual_rules == expected_rules

        text = claude_md.read_text()
        assert MARKER_START in text
        assert MARKER_END in text
        assert CONSOLE_URL in text

    def test_seeded_legacy_stub_is_removed_on_install(
        self, scope: Path, claude_md: Path, fake_assets
    ):
        # Given — the pre-migration stub skill file
        skills_dir = scope / "skills"
        skills_dir.mkdir()
        stub = skills_dir / "nrev-lite-gtm.md"
        stub.write_text("legacy stub")

        # When
        install_claude_assets(scope, claude_md, CONSOLE_URL)

        # Then
        assert not stub.exists()

    def test_stale_renamed_skill_is_swept_and_new_tree_present(
        self, scope: Path, claude_md: Path, fake_assets
    ):
        # Given — a skill that shipped in a previous version but not this one
        stale = scope / "skills" / "nrev-lite-old-skill"
        stale.mkdir(parents=True)
        (stale / "SKILL.md").write_text("old")

        # When
        install_claude_assets(scope, claude_md, CONSOLE_URL)

        # Then — stale folder gone, packaged skills present
        assert not stale.exists()
        assert (
            scope / "skills" / "nrev-lite-apollo-enrichment" / "SKILL.md"
        ).exists()

    def test_user_authored_skill_is_preserved_across_install(
        self, scope: Path, claude_md: Path, fake_assets
    ):
        # Given
        user_skill = scope / "skills" / "my-custom-skill"
        user_skill.mkdir(parents=True)
        (user_skill / "SKILL.md").write_text("my custom body")

        # When
        install_claude_assets(scope, claude_md, CONSOLE_URL)

        # Then
        assert (user_skill / "SKILL.md").read_text() == "my custom body"

    def test_legacy_claude_md_block_without_markers_is_not_migrated(
        self, scope: Path, claude_md: Path, fake_assets
    ):
        # Given — a pre-sentinel nrev-flavored block lives in CLAUDE.md.
        # HLD §11 accepted artifact: old block remains, new marker-wrapped
        # block is appended. Users see two nrev blocks until they clean up.
        legacy = (
            "# nrev-lite — Agent-Native GTM Execution Platform\n\n"
            "Old prose from the v1 setup-claude. No sentinel markers.\n"
        )
        claude_md.write_text(legacy)

        # When
        install_claude_assets(scope, claude_md, CONSOLE_URL)

        # Then
        text = claude_md.read_text()
        assert "Old prose from the v1 setup-claude." in text  # not migrated
        assert MARKER_START in text  # new block appended
        assert MARKER_END in text
        assert text.index("Old prose") < text.index(MARKER_START)


# =============================================================================
# nrev-lite setup-claude — CLI-level scenarios
# =============================================================================


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect HOME + cwd + config dirs for the CLI subprocess-equivalent.

    Every test gets a clean ~/.claude, a clean CWD, and no real credentials.
    """
    home = tmp_path / "home"
    home.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(cwd)
    # Insulate the CLI from real user config.
    monkeypatch.setenv("NREV_LITE_HOME", str(home / ".nrev-lite"))
    return {"home": home, "cwd": cwd}


class TestSetupClaudeCli:
    """`setup-claude` is a thin wrapper over install_claude_assets. It resolves
    the console URL via `resolve_console_url`; a None return is its signal
    that auth is missing (per LLD T7)."""

    def test_exits_with_error_when_auth_missing(self, cli_env, fake_assets):
        from nrev_lite.cli.setup import setup_claude

        runner = CliRunner()

        with patch(
            "nrev_lite.cli.setup.resolve_console_url", return_value=None
        ):
            result = runner.invoke(setup_claude, [])

        assert result.exit_code != 0
        output = result.output.lower()
        assert "auth login" in output or "authenticate" in output

        # No filesystem writes — .claude/ was not created.
        assert not (cli_env["home"] / ".claude" / "skills").exists()

    def test_delegates_to_install_function_when_auth_present(
        self, cli_env, fake_assets
    ):
        from nrev_lite.cli.setup import setup_claude

        runner = CliRunner()

        with patch(
            "nrev_lite.cli.setup.resolve_console_url",
            return_value=CONSOLE_URL,
        ):
            result = runner.invoke(setup_claude, [])

        assert result.exit_code == 0, result.output
        skills_dir = cli_env["home"] / ".claude" / "skills"
        assert (
            skills_dir / "nrev-lite-apollo-enrichment" / "SKILL.md"
        ).exists()
        claude_md = cli_env["home"] / ".claude" / "CLAUDE.md"
        assert claude_md.exists()
        assert MARKER_START in claude_md.read_text()

    def test_project_flag_installs_into_cwd_not_home(
        self, cli_env, fake_assets
    ):
        from nrev_lite.cli.setup import setup_claude

        runner = CliRunner()

        with patch(
            "nrev_lite.cli.setup.resolve_console_url",
            return_value=CONSOLE_URL,
        ):
            result = runner.invoke(setup_claude, ["--project"])

        assert result.exit_code == 0, result.output
        # Project scope: cwd/.claude
        assert (
            cli_env["cwd"]
            / ".claude"
            / "skills"
            / "nrev-lite-apollo-enrichment"
            / "SKILL.md"
        ).exists()
        assert (cli_env["cwd"] / "CLAUDE.md").exists()
        # Global scope untouched
        assert not (cli_env["home"] / ".claude" / "skills").exists()


# =============================================================================
# nrev-lite init Step 3 — three-state outcome reporting (HLD §8)
# =============================================================================


class TestInitThreeStateOutcome:
    """Exercises Step 3 of `nrev-lite init`. Auth + MCP steps are stubbed —
    this suite is only about the install outcome mapping."""

    def _runner_with_stubbed_preamble(
        self, *, install_return=None, install_raises=None
    ):
        """Set up an init invocation where auth + MCP succeed and the
        install function is replaced with a stub controlled by the test.
        """
        stubs = [
            patch(
                "nrev_lite.cli.init.shutil.which",
                return_value="/usr/local/bin/claude",
            ),
            patch(
                "nrev_lite.cli.init.is_authenticated", return_value=True
            ),
            patch(
                "nrev_lite.cli.init.load_credentials",
                return_value={
                    "user_info": {
                        "email": "test@nrev.ai",
                        "tenant": "tenant-42",
                    }
                },
            ),
            patch(
                "nrev_lite.cli.init.resolve_console_url",
                return_value=CONSOLE_URL,
            ),
            patch(
                "nrev_lite.cli.init._is_already_registered",
                return_value=True,
            ),
            patch(
                "nrev_lite.cli.init._verify_server_reachable",
                return_value=True,
            ),
        ]
        if install_raises is not None:
            install_patch = patch(
                "nrev_lite.cli.init.install_claude_assets",
                side_effect=install_raises,
            )
        else:
            install_patch = patch(
                "nrev_lite.cli.init.install_claude_assets",
                return_value=install_return,
            )
        stubs.append(install_patch)
        return stubs

    def test_complete_outcome_when_install_succeeds_with_all_writes(
        self, cli_env
    ):
        from nrev_lite.cli._claude_install import InstallSummary
        from nrev_lite.cli.init import init

        summary = InstallSummary(
            skills_written=3, rules_written=2, claude_md_action="created"
        )

        stubs = self._runner_with_stubbed_preamble(install_return=summary)
        runner = CliRunner()

        with _stack(stubs):
            result = runner.invoke(init, ["--skip-auth"])

        assert result.exit_code == 0
        assert "setup complete" in result.output.lower()

    def test_partial_outcome_when_claude_md_skipped(self, cli_env):
        from nrev_lite.cli._claude_install import InstallSummary
        from nrev_lite.cli.init import init

        summary = InstallSummary(
            skills_written=3, rules_written=2, claude_md_action="skipped"
        )

        stubs = self._runner_with_stubbed_preamble(install_return=summary)
        runner = CliRunner()

        with _stack(stubs):
            result = runner.invoke(init, ["--skip-auth"])

        # Non-zero exit for partial outcome (HLD §8).
        assert result.exit_code != 0
        output = result.output.lower()
        assert "partial" in output or "claude.md" in output
        assert "re-run" in output or "retry" in output

    def test_failure_outcome_when_install_raises(self, cli_env):
        from nrev_lite.cli.init import init

        err = ClaudeInstallError(
            "Permission denied: /home/.../claude/skills"
        )

        stubs = self._runner_with_stubbed_preamble(install_raises=err)
        runner = CliRunner()

        with _stack(stubs):
            result = runner.invoke(init, ["--skip-auth"])

        assert result.exit_code != 0
        output = result.output.lower()
        assert "failed" in output or "error" in output
        assert (
            "permission denied" in output
            or "nrev-lite setup-claude" in output
            or "re-run" in output
        )


# =============================================================================
# Helpers
# =============================================================================


class _stack:
    """Apply a list of patchers in sequence, teardown in reverse."""

    def __init__(self, patchers):
        self._patchers = patchers

    def __enter__(self):
        for p in self._patchers:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patchers):
            p.stop()
        return False
