"""Make the repo's bundled Agent Skills discoverable by the CLI connectors.

This project ships a set of Skills under ``claude_skills/`` at the repo root
(each ``claude_skills/<name>/SKILL.md`` plus optional ``scripts/`` and
``references/``). Both CLI backends the app drives — ``claude`` (Claude Code)
and ``opencode`` — auto-discover Skills, but only from a fixed set of
locations:

- ``<cwd>/.claude/skills/<name>/`` — project scope, walked up to the git root
- ``~/.claude/skills/<name>/``      — personal / global scope

``claude_skills/`` is none of those, so without help the bundled Skills are
invisible to every review / fix run. This module bridges the gap by linking
each bundled Skill into the personal/global skills directory
(``~/.claude/skills/``), which *both* CLIs read regardless of the working
directory. That covers every flow:

- opencode (all flows) and the Claude *fix* flow discover them natively.
- the Claude read-only review flow discovers them too, now that the adapter
  no longer forces ``--bare`` (which would skip Skill discovery).

The install is:

- **idempotent** — re-running only re-affirms links we already own; nothing
  accumulates and nothing is duplicated.
- **non-destructive** — a pre-existing ``~/.claude/skills/<name>`` that is *not*
  one of our links is left untouched, so a user's own global Skill of the same
  name always wins. The bundled Skills are added *in addition to* whatever the
  user already has globally.
- **best-effort** — any filesystem error is logged and skipped; a Skill-install
  failure must never stop the server from booting or a review from running.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Env override for the bundled-skills source directory. Absolute paths are used
# as-is; relative paths resolve against the repo root. Defaults to
# ``<repo>/claude_skills``.
_SKILLS_DIR_ENV = "GH_REVIEW_SKILLS_DIR"


def _repo_root() -> Path:
    # app/adapters/ai/_skills.py -> parents[3] == repo root.
    return Path(__file__).resolve().parents[3]


def bundled_skills_dir() -> Path:
    """Directory holding the repo's bundled Skills (``<repo>/claude_skills``)."""
    override = os.environ.get(_SKILLS_DIR_ENV, "").strip()
    if override:
        p = Path(override).expanduser()
        return p if p.is_absolute() else (_repo_root() / p)
    return _repo_root() / "claude_skills"


def global_skills_dir() -> Path:
    """Personal / global skills directory that both CLIs read.

    Honours ``CLAUDE_CONFIG_DIR`` (the env var the ``claude`` CLI uses to
    relocate ``~/.claude``); otherwise falls back to ``~/.claude``.
    """
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    base = Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"
    return base / "skills"


@dataclass(frozen=True)
class Skill:
    """A bundled Skill: its identifier, one-line description, and source dir."""

    name: str
    description: str
    path: Path  # the skill directory containing SKILL.md


@dataclass
class SkillInstallResult:
    """Outcome of :func:`install_bundled_skills`, for logging / tests."""

    installed: list[str] = field(default_factory=list)        # linked or already ours
    skipped_conflict: list[str] = field(default_factory=list)  # user owns a same-named entry
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _parse_frontmatter(skill_md: Path) -> dict[str, str]:
    """Extract simple ``key: value`` pairs from a SKILL.md YAML frontmatter block.

    Deliberately tiny: Skill frontmatter only needs ``name`` and
    ``description``, both single-line scalars, so this avoids pulling in a YAML
    dependency. Anything it can't parse degrades to an empty dict and the
    caller falls back to the directory name.
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    fields: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        # Only top-level scalar keys (no leading indentation) — ignore nested
        # YAML so a stray "foo: bar" inside a value block isn't misread.
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            fields[key.strip().lower()] = value.strip()
    return fields


def discover_skills() -> list[Skill]:
    """List the bundled Skills (``<dir>/SKILL.md`` present), sorted by name."""
    root = bundled_skills_dir()
    if not root.is_dir():
        return []
    skills: list[Skill] = []
    for child in sorted(root.iterdir()):
        skill_md = child / "SKILL.md"
        if not (child.is_dir() and skill_md.is_file()):
            continue
        fields = _parse_frontmatter(skill_md)
        skills.append(
            Skill(
                name=fields.get("name") or child.name,
                description=fields.get("description", ""),
                path=child,
            )
        )
    return skills


def install_bundled_skills() -> SkillInstallResult:
    """Link bundled Skills into the global skills dir so both CLIs see them.

    Returns a :class:`SkillInstallResult`. Never raises — every filesystem
    error is captured so a Skill problem can't take down the server.
    """
    result = SkillInstallResult()
    skills = discover_skills()
    if not skills:
        logger.info("skills | no bundled skills found at %s", bundled_skills_dir())
        return result

    target_root = global_skills_dir()
    try:
        target_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = f"cannot create global skills dir {target_root}: {exc}"
        logger.warning("skills | %s", msg)
        result.errors.append(msg)
        return result

    for skill in skills:
        # Mirror the on-disk layout: the link name is the source directory name
        # (which the CLIs use as the skill identifier).
        link = target_root / skill.path.name
        source = skill.path.resolve()
        try:
            if link.is_symlink():
                if link.resolve() == source:
                    result.installed.append(skill.path.name)  # already ours
                else:
                    result.skipped_conflict.append(skill.path.name)  # user's link wins
                continue
            if link.exists():
                result.skipped_conflict.append(skill.path.name)  # real dir/file — never clobber
                continue
            link.symlink_to(source, target_is_directory=True)
            result.installed.append(skill.path.name)
        except OSError as exc:
            # Symlinks can be unsupported (e.g. some Windows configs). Fall back
            # to a plain copy so the skill is still discoverable.
            if _try_copy(source, link):
                result.installed.append(skill.path.name)
            else:
                msg = f"failed to install skill {skill.path.name!r}: {exc}"
                logger.warning("skills | %s", msg)
                result.errors.append(msg)

    logger.info(
        "skills | install complete | dir=%s installed=%s conflicts=%s errors=%d",
        target_root, result.installed, result.skipped_conflict, len(result.errors),
    )
    return result


def _try_copy(source: Path, link: Path) -> bool:
    """Best-effort ``copytree`` fallback when symlinking is unavailable."""
    try:
        if link.exists():
            return False
        shutil.copytree(source, link)
        return True
    except OSError:
        return False


__all__ = [
    "Skill",
    "SkillInstallResult",
    "bundled_skills_dir",
    "discover_skills",
    "global_skills_dir",
    "install_bundled_skills",
]
