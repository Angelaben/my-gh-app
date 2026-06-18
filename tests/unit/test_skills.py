"""Tests for :mod:`app.adapters.ai._skills` — bundled-Skill discovery + install.

These exercise the wiring that makes the repo's ``claude_skills/`` directory
discoverable by the ``claude`` and ``opencode`` CLIs (both read
``~/.claude/skills/``). The install target is redirected to a tmp dir via
``CLAUDE_CONFIG_DIR`` so nothing touches the real home directory.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.ai import _skills


def _write_skill(root: Path, name: str, description: str = "Does a thing.") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


@pytest.fixture
def bundled(tmp_path, monkeypatch):
    """A fake bundled-skills source dir, wired via GH_REVIEW_SKILLS_DIR."""
    src = tmp_path / "claude_skills"
    src.mkdir()
    monkeypatch.setenv("GH_REVIEW_SKILLS_DIR", str(src))
    return src


@pytest.fixture
def global_dir(tmp_path, monkeypatch):
    """Redirect the global skills dir into tmp via CLAUDE_CONFIG_DIR."""
    config = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config / "skills"


def test_discover_skills_parses_frontmatter(bundled):
    _write_skill(bundled, "code-reviewer", "Reviews code carefully.")
    skills = _skills.discover_skills()
    assert [s.name for s in skills] == ["code-reviewer"]
    assert skills[0].description == "Reviews code carefully."


def test_discover_skips_dirs_without_skill_md(bundled):
    (bundled / "not-a-skill").mkdir()
    _write_skill(bundled, "real")
    assert [s.name for s in _skills.discover_skills()] == ["real"]


def test_discover_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_REVIEW_SKILLS_DIR", str(tmp_path / "nope"))
    assert _skills.discover_skills() == []


def test_install_symlinks_into_global_dir(bundled, global_dir):
    src = _write_skill(bundled, "code-reviewer")
    result = _skills.install_bundled_skills()

    assert result.ok
    assert result.installed == ["code-reviewer"]
    link = global_dir / "code-reviewer"
    assert link.is_symlink()
    assert link.resolve() == src.resolve()
    # SKILL.md is reachable through the link.
    assert (link / "SKILL.md").is_file()


def test_install_is_idempotent(bundled, global_dir):
    _write_skill(bundled, "code-reviewer")
    first = _skills.install_bundled_skills()
    second = _skills.install_bundled_skills()
    assert first.installed == ["code-reviewer"]
    assert second.installed == ["code-reviewer"]  # re-affirmed, not duplicated
    assert list((global_dir).iterdir()) == [global_dir / "code-reviewer"]


def test_install_does_not_clobber_user_skill(bundled, global_dir):
    """A pre-existing real dir of the same name (the user's own global Skill)
    must be left untouched — bundled Skills are added *in addition*."""
    _write_skill(bundled, "code-reviewer", "Bundled version.")
    user_skill = global_dir / "code-reviewer"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text("---\nname: code-reviewer\n---\nMINE\n")

    result = _skills.install_bundled_skills()

    assert result.skipped_conflict == ["code-reviewer"]
    assert result.installed == []
    assert not user_skill.is_symlink()
    assert "MINE" in (user_skill / "SKILL.md").read_text()


def test_install_no_skills_is_noop(bundled, global_dir):
    result = _skills.install_bundled_skills()
    assert result.ok
    assert result.installed == []
    assert not global_dir.exists()


def test_real_bundled_code_reviewer_is_discoverable(monkeypatch):
    """The repo actually ships claude_skills/code-reviewer — guard against it
    silently disappearing or losing its frontmatter."""
    monkeypatch.delenv("GH_REVIEW_SKILLS_DIR", raising=False)
    names = {s.name for s in _skills.discover_skills()}
    assert "code-reviewer" in names
