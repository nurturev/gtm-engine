"""Unit tests for `nrev_lite.cli._claude_install`.

Tests the filesystem-only install module that powers both `nrev-lite init`
Step 3 and the diagnostic `nrev-lite setup-claude` command.

Scope (see LLD §12.1):
    - _refresh_claude_md — full behavior matrix (HLD §6)
    - _sweep_nrev_skills — prefix-scoped removal
    - _write_skills — packaged copy with `nrev-lite-` prefix
    - _refresh_rules — folder-scoped removal + rewrite
    - _delete_legacy_stub — name-based file deletion
    - install_claude_assets — orchestrator happy path, idempotency, errors,
      CLAUDE.md-skipped partial path

Mocking policy (unit-testing blueprint §6):
    - tmp_path for scope dir + CLAUDE.md — real filesystem, no mocks
    - monkeypatch `_resolve_packaged_assets` to inject a deterministic tree
    - monkeypatch `Path.write_text` only to simulate OSError
    - No other mocks
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from nrev_lite.cli import _claude_install
from nrev_lite.cli._claude_install import (
    ClaudeInstallError,
    InstallSummary,
    _delete_legacy_stub,
    _refresh_claude_md,
    _refresh_rules,
    _sweep_nrev_skills,
    _write_skills,
    install_claude_assets,
)

MARKER_START = "<!-- nrev-lite:managed:start -->"
MARKER_END = "<!-- nrev-lite:managed:end -->"

CONSOLE_URL = "https://api.nrev.ai/console/tenant-42"


# =============================================================================
# _refresh_claude_md — HLD §6 behavior matrix
# =============================================================================


class TestRefreshClaudeMdWhenFileMissing:
    def test_creates_file_with_markers_wrapping_substituted_template(
        self, claude_md_path: Path
    ):
        # Given — no CLAUDE.md exists

        # When
        action = _refresh_claude_md(claude_md_path, CONSOLE_URL)

        # Then
        assert action == "created"
        assert claude_md_path.exists()
        text = claude_md_path.read_text()
        assert MARKER_START in text
        assert MARKER_END in text
        assert text.index(MARKER_START) < text.index(MARKER_END)
        assert "{{console_url}}" not in text
        assert CONSOLE_URL in text


class TestRefreshClaudeMdWhenFileExistsWithoutMarkers:
    def test_appends_managed_block_preserving_prior_content(
        self, claude_md_path: Path
    ):
        # Given — existing CLAUDE.md with user-authored content
        original = "# My personal rules\n\nI like strict types.\n"
        claude_md_path.write_text(original)

        # When
        action = _refresh_claude_md(claude_md_path, CONSOLE_URL)

        # Then
        assert action == "appended"
        text = claude_md_path.read_text()
        assert text.startswith(original.rstrip())
        assert MARKER_START in text
        assert MARKER_END in text
        # Appended block comes AFTER the original content.
        assert text.index("My personal rules") < text.index(MARKER_START)


class TestRefreshClaudeMdWhenBothMarkersPresent:
    def test_replaces_content_between_markers_only(self, claude_md_path: Path):
        # Given — existing CLAUDE.md with a managed region plus surrounding content
        preamble = "# User preamble\n\nKeep this.\n"
        managed_old = f"{MARKER_START}\n# stale content\n{MARKER_END}"
        postamble = "\n\n# User footer\nKeep this too.\n"
        claude_md_path.write_text(preamble + "\n" + managed_old + postamble)

        # When
        action = _refresh_claude_md(claude_md_path, CONSOLE_URL)

        # Then
        assert action == "replaced"
        text = claude_md_path.read_text()
        assert "# User preamble" in text
        assert "Keep this." in text
        assert "# User footer" in text
        assert "Keep this too." in text
        assert "# stale content" not in text
        assert CONSOLE_URL in text
        # Exactly one pair of markers remains.
        assert text.count(MARKER_START) == 1
        assert text.count(MARKER_END) == 1


class TestRefreshClaudeMdWhenOnlyStartMarkerPresent:
    def test_treats_as_malformed_and_appends(
        self, claude_md_path: Path, capsys
    ):
        # Given — dangling start marker (no matching end)
        claude_md_path.write_text(
            f"# user content\n\n{MARKER_START}\n# broken region\n"
        )

        # When
        action = _refresh_claude_md(claude_md_path, CONSOLE_URL)

        # Then
        assert action == "appended"
        text = claude_md_path.read_text()
        assert "# broken region" in text  # not overwritten
        assert text.count(MARKER_END) == 1  # the one we appended
        # Warning surfaces to the user.
        captured = capsys.readouterr()
        assert "malformed" in (captured.out + captured.err).lower()


class TestRefreshClaudeMdWhenOnlyEndMarkerPresent:
    def test_treats_as_malformed_and_appends(
        self, claude_md_path: Path, capsys
    ):
        claude_md_path.write_text(f"# user content\n\n{MARKER_END}\n")

        action = _refresh_claude_md(claude_md_path, CONSOLE_URL)

        assert action == "appended"
        captured = capsys.readouterr()
        assert "malformed" in (captured.out + captured.err).lower()


class TestRefreshClaudeMdWhenEndBeforeStart:
    def test_treats_as_malformed_and_appends(
        self, claude_md_path: Path, capsys
    ):
        claude_md_path.write_text(
            f"{MARKER_END}\nsome text\n{MARKER_START}\n"
        )

        action = _refresh_claude_md(claude_md_path, CONSOLE_URL)

        assert action == "appended"
        captured = capsys.readouterr()
        assert "malformed" in (captured.out + captured.err).lower()


class TestRefreshClaudeMdWhenMultipleMarkerPairs:
    def test_replaces_first_pair_only_and_warns(
        self, claude_md_path: Path, capsys
    ):
        # Given — two well-formed managed regions (e.g. user duplicated by mistake)
        block_a = f"{MARKER_START}\n# first stale\n{MARKER_END}"
        block_b = f"{MARKER_START}\n# second stale\n{MARKER_END}"
        claude_md_path.write_text(f"{block_a}\n\n{block_b}\n")

        # When
        action = _refresh_claude_md(claude_md_path, CONSOLE_URL)

        # Then
        assert action == "replaced"
        text = claude_md_path.read_text()
        # First pair got rewritten; second pair untouched.
        assert "# first stale" not in text
        assert "# second stale" in text
        captured = capsys.readouterr()
        assert "multiple" in (captured.out + captured.err).lower()


class TestRefreshClaudeMdSubstitution:
    def test_placeholder_replaced_at_every_occurrence(
        self, claude_md_path: Path
    ):
        _refresh_claude_md(claude_md_path, CONSOLE_URL)

        text = claude_md_path.read_text()
        assert "{{console_url}}" not in text
        assert text.count(CONSOLE_URL) >= 1

    @pytest.mark.parametrize(
        "tricky_url",
        [
            "https://api.nrev.ai/console/tenant+special",
            "https://api.nrev.ai/console/tenant.with.dots",
            "https://api.nrev.ai/console/tenant$with$dollar",
            "https://api.nrev.ai/console/tenant\\backslash",
        ],
    )
    def test_regex_special_chars_inserted_literally(
        self, claude_md_path: Path, tricky_url: str
    ):
        _refresh_claude_md(claude_md_path, tricky_url)

        text = claude_md_path.read_text()
        assert tricky_url in text


class TestRefreshClaudeMdUnicodePreservation:
    def test_existing_utf8_content_round_trips(self, claude_md_path: Path):
        original = "# héllo 你好 🌀\n\nemoji: ✅\n"
        claude_md_path.write_text(original, encoding="utf-8")

        _refresh_claude_md(claude_md_path, CONSOLE_URL)

        text = claude_md_path.read_text(encoding="utf-8")
        assert "héllo 你好 🌀" in text
        assert "emoji: ✅" in text


class TestRefreshClaudeMdOsErrorIsNonFatal:
    def test_write_failure_returns_skipped_and_does_not_raise(
        self, claude_md_path: Path, capsys
    ):
        # Given — write_text raises OSError (simulate read-only filesystem)
        with patch.object(
            Path, "write_text", side_effect=OSError("read-only")
        ):
            # When / Then — must not raise
            action = _refresh_claude_md(claude_md_path, CONSOLE_URL)

        assert action == "skipped"
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "claude.md" in combined.lower() or "read-only" in combined.lower()


# =============================================================================
# _sweep_nrev_skills
# =============================================================================


class TestSweepNrevSkills:
    def _make_skill(self, parent: Path, name: str) -> Path:
        d = parent / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n\n{name}\n")
        return d

    def test_removes_every_nrev_prefixed_folder(self, tmp_path: Path):
        skills = tmp_path / "skills"
        self._make_skill(skills, "nrev-lite-apollo-enrichment")
        self._make_skill(skills, "nrev-lite-provider-selection")

        removed = _sweep_nrev_skills(skills)

        assert removed == 2
        assert not (skills / "nrev-lite-apollo-enrichment").exists()
        assert not (skills / "nrev-lite-provider-selection").exists()

    def test_leaves_non_nrev_folders_untouched(self, tmp_path: Path):
        skills = tmp_path / "skills"
        self._make_skill(skills, "nrev-lite-apollo-enrichment")
        self._make_skill(skills, "my-custom-skill")
        self._make_skill(skills, "another-vendor-skill")

        removed = _sweep_nrev_skills(skills)

        assert removed == 1
        assert (skills / "my-custom-skill").exists()
        assert (skills / "another-vendor-skill").exists()

    def test_does_not_match_partial_prefix(self, tmp_path: Path):
        # "nrev-lite" (no trailing dash) must not match the `nrev-lite-` prefix.
        skills = tmp_path / "skills"
        self._make_skill(skills, "nrev-lite")
        self._make_skill(skills, "some-nrev-lite-adjacent")

        removed = _sweep_nrev_skills(skills)

        assert removed == 0
        assert (skills / "nrev-lite").exists()
        assert (skills / "some-nrev-lite-adjacent").exists()

    def test_leaves_legacy_stub_file_for_dedicated_helper(
        self, tmp_path: Path
    ):
        # Legacy stub is a FILE, not a folder — sweep only removes folders.
        skills = tmp_path / "skills"
        skills.mkdir()
        stub = skills / "nrev-lite-gtm.md"
        stub.write_text("stub")

        _sweep_nrev_skills(skills)

        assert stub.exists()  # _delete_legacy_stub handles this one

    def test_no_op_when_skills_dir_missing(self, tmp_path: Path):
        missing = tmp_path / "skills"
        assert not missing.exists()

        removed = _sweep_nrev_skills(missing)

        assert removed == 0

    def test_no_op_when_skills_dir_empty(self, tmp_path: Path):
        skills = tmp_path / "skills"
        skills.mkdir()

        removed = _sweep_nrev_skills(skills)

        assert removed == 0


# =============================================================================
# _write_skills
# =============================================================================


class TestWriteSkills:
    def test_every_packaged_skill_lands_with_nrev_lite_prefix(
        self, fake_packaged_assets, tmp_path: Path
    ):
        skills_src, _ = fake_packaged_assets
        target = tmp_path / "skills"

        count = _write_skills(skills_src, target)

        assert count == 3
        assert (target / "nrev-lite-apollo-enrichment" / "SKILL.md").exists()
        assert (
            target / "nrev-lite-rocketreach-enrichment" / "SKILL.md"
        ).exists()
        assert (target / "nrev-lite-provider-selection" / "SKILL.md").exists()

    def test_skill_body_preserved_verbatim(
        self, fake_packaged_assets, tmp_path: Path
    ):
        skills_src, _ = fake_packaged_assets
        target = tmp_path / "skills"

        _write_skills(skills_src, target)

        written = (
            target / "nrev-lite-apollo-enrichment" / "SKILL.md"
        ).read_text()
        source = (skills_src / "apollo-enrichment" / "SKILL.md").read_text()
        assert written == source


# =============================================================================
# _refresh_rules
# =============================================================================


class TestRefreshRules:
    """Contract: _refresh_rules(rules_src, target) where target is the FULL
    nrev-lite folder path (e.g. <scope>/rules/nrev-lite), not the parent.

    Inferred from the orchestrator call site:
        _refresh_rules(rules_src, scope_dir / "rules" / RULES_SUBDIR)
    """

    def test_preexisting_nrev_lite_rules_folder_is_replaced(
        self, fake_packaged_assets, tmp_path: Path
    ):
        _, rules_src = fake_packaged_assets
        target = tmp_path / "rules" / "nrev-lite"
        stale = target / "stale-old-rule.md"
        stale.parent.mkdir(parents=True)
        stale.write_text("stale content")

        count = _refresh_rules(rules_src, target)

        assert count == 2
        assert not stale.exists()
        assert (target / "security.md").exists()
        assert (target / "enrichment.md").exists()

    def test_sibling_rule_files_outside_nrev_lite_untouched(
        self, fake_packaged_assets, tmp_path: Path
    ):
        _, rules_src = fake_packaged_assets
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        user_rule = rules_dir / "my-user-rule.md"
        user_rule.write_text("do not touch")
        other_vendor = rules_dir / "other-vendor" / "vendor.md"
        other_vendor.parent.mkdir()
        other_vendor.write_text("do not touch either")
        target = rules_dir / "nrev-lite"

        _refresh_rules(rules_src, target)

        assert user_rule.exists()
        assert user_rule.read_text() == "do not touch"
        assert other_vendor.exists()
        assert other_vendor.read_text() == "do not touch either"

    def test_creates_target_when_parent_missing(
        self, fake_packaged_assets, tmp_path: Path
    ):
        _, rules_src = fake_packaged_assets
        target = tmp_path / "rules" / "nrev-lite"
        # Neither parent `rules/` nor `nrev-lite/` exists yet.

        count = _refresh_rules(rules_src, target)

        assert count == 2
        assert (target / "security.md").exists()


# =============================================================================
# _delete_legacy_stub
# =============================================================================


class TestDeleteLegacyStub:
    def test_deletes_file_when_present(self, tmp_path: Path):
        skills = tmp_path / "skills"
        skills.mkdir()
        stub = skills / "nrev-lite-gtm.md"
        stub.write_text("old stub")

        _delete_legacy_stub(skills)

        assert not stub.exists()

    def test_does_not_delete_folder_of_same_name(self, tmp_path: Path):
        skills = tmp_path / "skills"
        skills.mkdir()
        folder = skills / "nrev-lite-gtm.md"
        folder.mkdir()
        inner = folder / "something.md"
        inner.write_text("user content")

        _delete_legacy_stub(skills)

        assert folder.is_dir()
        assert inner.exists()

    def test_no_op_when_absent(self, tmp_path: Path):
        skills = tmp_path / "skills"
        skills.mkdir()

        # Must not raise.
        _delete_legacy_stub(skills)

    def test_no_op_when_skills_dir_absent(self, tmp_path: Path):
        skills = tmp_path / "skills"
        assert not skills.exists()

        _delete_legacy_stub(skills)  # must not raise


# =============================================================================
# install_claude_assets — orchestrator
# =============================================================================


class TestInstallClaudeAssetsPreconditions:
    def test_raises_when_scope_dir_is_not_dot_claude(
        self, tmp_path: Path, claude_md_path: Path, patched_assets
    ):
        bogus = tmp_path / "not-a-claude-dir"
        bogus.mkdir()

        with pytest.raises(ClaudeInstallError):
            install_claude_assets(bogus, claude_md_path, CONSOLE_URL)

    def test_raises_when_console_url_empty(
        self, scope_dir: Path, claude_md_path: Path, patched_assets
    ):
        with pytest.raises(ClaudeInstallError):
            install_claude_assets(scope_dir, claude_md_path, "")


class TestInstallClaudeAssetsHappyPath:
    def test_fresh_install_returns_complete_summary(
        self,
        scope_dir: Path,
        claude_md_path: Path,
        patched_assets,
    ):
        summary = install_claude_assets(scope_dir, claude_md_path, CONSOLE_URL)

        assert isinstance(summary, InstallSummary)
        assert summary.skills_written == 3
        assert summary.rules_written == 2
        assert summary.claude_md_action == "created"

    def test_all_packaged_skills_written_with_prefix(
        self,
        scope_dir: Path,
        claude_md_path: Path,
        patched_assets,
    ):
        install_claude_assets(scope_dir, claude_md_path, CONSOLE_URL)

        skills_dir = scope_dir / "skills"
        assert (
            skills_dir / "nrev-lite-apollo-enrichment" / "SKILL.md"
        ).exists()
        assert (
            skills_dir / "nrev-lite-rocketreach-enrichment" / "SKILL.md"
        ).exists()
        assert (
            skills_dir / "nrev-lite-provider-selection" / "SKILL.md"
        ).exists()

    def test_all_packaged_rules_written_under_nrev_lite_folder(
        self,
        scope_dir: Path,
        claude_md_path: Path,
        patched_assets,
    ):
        install_claude_assets(scope_dir, claude_md_path, CONSOLE_URL)

        rules_dir = scope_dir / "rules" / "nrev-lite"
        assert (rules_dir / "security.md").exists()
        assert (rules_dir / "enrichment.md").exists()

    def test_claude_md_has_marker_region_with_resolved_console_url(
        self,
        scope_dir: Path,
        claude_md_path: Path,
        patched_assets,
    ):
        install_claude_assets(scope_dir, claude_md_path, CONSOLE_URL)

        text = claude_md_path.read_text()
        assert MARKER_START in text
        assert MARKER_END in text
        assert CONSOLE_URL in text
        assert "{{console_url}}" not in text


class TestInstallClaudeAssetsIdempotency:
    def test_two_runs_produce_identical_filesystem_state(
        self,
        scope_dir: Path,
        claude_md_path: Path,
        patched_assets,
    ):
        first = install_claude_assets(scope_dir, claude_md_path, CONSOLE_URL)
        snapshot_first = _snapshot_tree(scope_dir, claude_md_path)

        second = install_claude_assets(scope_dir, claude_md_path, CONSOLE_URL)
        snapshot_second = _snapshot_tree(scope_dir, claude_md_path)

        # Skills + rules counts are unchanged on re-run.
        assert second.skills_written == first.skills_written
        assert second.rules_written == first.rules_written
        # Second run replaces instead of creating.
        assert first.claude_md_action == "created"
        assert second.claude_md_action == "replaced"
        # File content identical across runs.
        assert snapshot_first == snapshot_second


class TestInstallClaudeAssetsRenameSimulation:
    def test_removed_skill_folder_is_swept_on_reinstall(
        self,
        scope_dir: Path,
        claude_md_path: Path,
        patched_assets,
    ):
        # Seed an older-version skill that no longer ships.
        stale = scope_dir / "skills" / "nrev-lite-old-discontinued"
        stale.mkdir(parents=True)
        (stale / "SKILL.md").write_text("old content")

        install_claude_assets(scope_dir, claude_md_path, CONSOLE_URL)

        assert not stale.exists()

    def test_user_authored_skill_is_preserved(
        self,
        scope_dir: Path,
        claude_md_path: Path,
        patched_assets,
    ):
        user_skill = scope_dir / "skills" / "my-custom-skill"
        user_skill.mkdir(parents=True)
        (user_skill / "SKILL.md").write_text("user content")

        install_claude_assets(scope_dir, claude_md_path, CONSOLE_URL)

        assert (user_skill / "SKILL.md").exists()
        assert (user_skill / "SKILL.md").read_text() == "user content"


class TestInstallClaudeAssetsLegacyStubCleanup:
    def test_legacy_stub_is_removed(
        self,
        scope_dir: Path,
        claude_md_path: Path,
        patched_assets,
    ):
        skills_dir = scope_dir / "skills"
        skills_dir.mkdir()
        stub = skills_dir / "nrev-lite-gtm.md"
        stub.write_text("legacy stub")

        install_claude_assets(scope_dir, claude_md_path, CONSOLE_URL)

        assert not stub.exists()


class TestInstallClaudeAssetsFailureModes:
    def test_packaged_assets_unreachable_raises(
        self,
        scope_dir: Path,
        claude_md_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # LLD §4: "Packaged assets missing" must surface as ClaudeInstallError,
        # not a raw ModuleNotFoundError. We trigger that path by making the
        # resources lookup fail.
        def boom(_name):
            raise ModuleNotFoundError("nrev_lite.resources.claude_assets")

        monkeypatch.setattr(_claude_install.resources, "files", boom)

        with pytest.raises(ClaudeInstallError):
            install_claude_assets(scope_dir, claude_md_path, CONSOLE_URL)

    def test_packaged_assets_empty_tree_raises(
        self,
        scope_dir: Path,
        claude_md_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # Assets directory exists but is empty (no skills/ or rules/ subdirs).
        empty = tmp_path / "empty_assets"
        empty.mkdir()

        from contextlib import contextmanager

        @contextmanager
        def fake_as_file(_t):
            yield empty

        monkeypatch.setattr(
            _claude_install.resources, "files", lambda _n: empty
        )
        monkeypatch.setattr(
            _claude_install.resources, "as_file", fake_as_file
        )

        with pytest.raises(ClaudeInstallError):
            install_claude_assets(scope_dir, claude_md_path, CONSOLE_URL)

    def test_claude_md_write_failure_returns_partial_summary(
        self,
        scope_dir: Path,
        claude_md_path: Path,
        patched_assets,
    ):
        real_write_text = Path.write_text

        def selective_write(self, data, *args, **kwargs):
            if self == claude_md_path:
                raise OSError("read-only file")
            return real_write_text(self, data, *args, **kwargs)

        with patch.object(Path, "write_text", selective_write):
            summary = install_claude_assets(
                scope_dir, claude_md_path, CONSOLE_URL
            )

        # Skills + rules still installed, CLAUDE.md skipped.
        assert summary.skills_written == 3
        assert summary.rules_written == 2
        assert summary.claude_md_action == "skipped"

    def test_skills_write_failure_raises_install_error(
        self,
        scope_dir: Path,
        claude_md_path: Path,
        patched_assets,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # Simulate OSError on skills copy (e.g. permission denied).
        import shutil

        def boom(*args, **kwargs):
            raise OSError("permission denied: skills")

        monkeypatch.setattr(shutil, "copytree", boom)

        with pytest.raises(ClaudeInstallError):
            install_claude_assets(scope_dir, claude_md_path, CONSOLE_URL)

        # CLAUDE.md should NOT have been touched when skills failed first.
        assert not claude_md_path.exists()


# =============================================================================
# Helpers
# =============================================================================


def _snapshot_tree(scope_dir: Path, claude_md_path: Path) -> dict[str, str]:
    """Flatten (relative_path -> content) for every file in scope + CLAUDE.md.

    Used to prove idempotency: the second run must yield an identical
    mapping.
    """
    snap: dict[str, str] = {}
    for p in sorted(scope_dir.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(scope_dir))
            snap[f"scope/{rel}"] = p.read_text()
    if claude_md_path.exists():
        snap["CLAUDE.md"] = claude_md_path.read_text()
    return snap
