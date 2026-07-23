"""Runtime-settable configuration: review knobs + custom AI prompts.

Persisted next to the JSON cache (``.cache/settings.json`` and
``.cache/prompts.json``) so it never reaches the remote. Effective values
resolve as: **stored override > environment variable > built-in default**.

Prompt defaults live in :mod:`app.adapters.ai._prompts` and are *never*
mutated — "restore default" simply drops the stored override.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from app.adapters.ai._prompts import (
    ANALYZE_PROMPT,
    FIX_PROMPT,
    REVIEW_PROMPT,
    SYNTHESIZE_PROMPT,
)
from app.services._diff_splitter import DEFAULT_IGNORE_GLOBS

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
_SETTINGS_PATH = _CACHE_DIR / "settings.json"
_PROMPTS_PATH = _CACHE_DIR / "prompts.json"

# Default values for the review knobs (also the lower-/upper-bound guards).
DEFAULT_REVIEW_TIMEOUT = 300
# Single-call budget. Splitting a diff into independent sub-reviews loses
# cross-file context, so the default is sized for large-context models
# (~37K tokens ≈ well within Claude's 200K window) and splitting only kicks
# in for genuinely huge PRs. Lower it for small-context models.
DEFAULT_DIFF_MAX_CHARS = 150_000
# Max reviews to run in parallel. Primarily caps how many PR reviews the UI
# runs at once (the review queue); it also bounds the parallel sub-calls on the
# rare path where a diff exceeds DEFAULT_DIFF_MAX_CHARS and has to be split.
DEFAULT_MAX_CONCURRENCY = 3

# Seconds between two Live Review poll cycles (gh pr list per watched repo).
DEFAULT_LIVE_REVIEW_POLL_INTERVAL = 300

# key -> (env var, default, min, max)
_INT_SETTINGS: dict[str, tuple[str, int, int, int]] = {
    "review_timeout": ("REVIEW_TIMEOUT", DEFAULT_REVIEW_TIMEOUT, 30, 3600),
    "review_diff_max_chars": ("REVIEW_DIFF_MAX_CHARS", DEFAULT_DIFF_MAX_CHARS, 1000, 2_000_000),
    "review_max_concurrency": ("REVIEW_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY, 1, 16),
    "live_review_poll_interval": (
        "LIVE_REVIEW_POLL_INTERVAL", DEFAULT_LIVE_REVIEW_POLL_INTERVAL, 60, 86_400,
    ),
}

# key -> (env var, default). Env parses "0"/"false"/"no"/"off" as False.
_BOOL_SETTINGS: dict[str, tuple[str, bool]] = {
    # Second AI pass after a split review: consolidates per-chunk findings
    # (semantic dedupe + one coherent summary). Costs one extra provider call.
    "review_synthesis": ("REVIEW_SYNTHESIS", True),
}

DEFAULT_PROMPTS: dict[str, str] = {
    "review": REVIEW_PROMPT,
    "fix": FIX_PROMPT,
    "analyze": ANALYZE_PROMPT,
    "synthesize": SYNTHESIZE_PROMPT,
}

# Placeholders each prompt is rendered with (see BaseCLIAIAdapter). A custom
# prompt must use exactly these — no more, no fewer — or ``str.format`` breaks.
REQUIRED_PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "review": ("pr_number", "repo_full_name"),
    "analyze": ("pr_number", "repo_full_name"),
    "fix": ("pr_number", "repo_full_name", "comment_body"),
    "synthesize": ("pr_number", "repo_full_name"),
}

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


# --- low-level json io -------------------------------------------------------

def _read(path: Path) -> dict:
    try:
        if path.exists():
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("settings | read failed | %s | %s", path, exc)
    return {}


def _write(path: Path, data: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
    except OSError as exc:
        logger.warning("settings | write failed | %s | %s", path, exc)


# --- review settings ---------------------------------------------------------

def _env_int(env: str, default: int) -> int:
    """Parse an int env var, falling back to ``default`` unless it's > 0.

    Bounds (the ``lo``/``hi`` in ``_INT_SETTINGS``) are enforced only on the
    UI save path — env vars keep their original "any positive int" semantics
    so power users / tests can push values outside the UI-friendly range.
    """
    raw = os.getenv(env)
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return default
        if value > 0:
            return value
    return default


_FALSY = frozenset({"0", "false", "no", "off"})


def _env_bool(env: str, default: bool) -> bool:
    """Parse a boolean env var; unset/empty → ``default``."""
    raw = os.getenv(env)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in _FALSY


def _coerce_bool(value: object) -> bool:
    """Coerce a stored/UI value ("false", 0, True, …) into a bool."""
    if isinstance(value, str):
        return value.strip().lower() not in _FALSY
    return bool(value)


def _effective_globs(stored: dict) -> list[str]:
    if "review_ignore_globs" in stored:
        value = stored["review_ignore_globs"]
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str):
            return [p.strip() for p in value.split(",") if p.strip()]
    raw = os.getenv("REVIEW_IGNORE_GLOBS")
    if raw is None:
        return list(DEFAULT_IGNORE_GLOBS)
    return [p.strip() for p in raw.split(",") if p.strip()]


def get_review_settings() -> dict:
    """Effective review settings (stored override > env > default)."""
    stored = _read(_SETTINGS_PATH)
    out: dict = {}
    for key, (env, default, _lo, _hi) in _INT_SETTINGS.items():
        if key in stored:
            try:
                out[key] = int(stored[key])
                continue
            except (TypeError, ValueError):
                pass
        out[key] = _env_int(env, default)
    for key, (env, default) in _BOOL_SETTINGS.items():
        out[key] = _coerce_bool(stored[key]) if key in stored else _env_bool(env, default)
    out["review_ignore_globs"] = _effective_globs(stored)
    return out


def default_review_settings() -> dict:
    out: dict = {key: default for key, (_env, default, _lo, _hi) in _INT_SETTINGS.items()}
    out.update({key: default for key, (_env, default) in _BOOL_SETTINGS.items()})
    out["review_ignore_globs"] = list(DEFAULT_IGNORE_GLOBS)
    return out


def save_review_settings(partial: dict) -> dict:
    """Validate + persist the given subset of review settings; return effective."""
    stored = _read(_SETTINGS_PATH)
    for key, (_env, _default, lo, hi) in _INT_SETTINGS.items():
        if key not in partial or partial[key] is None:
            continue
        try:
            value = int(partial[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
        if not (lo <= value <= hi):
            raise ValueError(f"{key} must be between {lo} and {hi}")
        stored[key] = value
    for key in _BOOL_SETTINGS:
        if key in partial and partial[key] is not None:
            stored[key] = _coerce_bool(partial[key])
    if "review_ignore_globs" in partial:
        value = partial["review_ignore_globs"]
        if isinstance(value, str):
            value = [p.strip() for p in value.split(",") if p.strip()]
        if not isinstance(value, list):
            raise ValueError("review_ignore_globs must be a list or comma-separated string")
        stored["review_ignore_globs"] = [str(x).strip() for x in value if str(x).strip()]
    _write(_SETTINGS_PATH, stored)
    return get_review_settings()


def reset_review_settings() -> dict:
    _write(_SETTINGS_PATH, {})
    return get_review_settings()


# --- prompts -----------------------------------------------------------------

def get_prompt(name: str) -> str:
    """Effective prompt body for ``name`` (custom override or built-in default)."""
    default = DEFAULT_PROMPTS.get(name)
    if default is None:
        raise KeyError(name)
    custom = _read(_PROMPTS_PATH).get(name)
    return custom if isinstance(custom, str) and custom.strip() else default


def get_prompts() -> dict:
    """All prompts with their default + whether a custom override is active."""
    stored = _read(_PROMPTS_PATH)
    out: dict = {}
    for name, default in DEFAULT_PROMPTS.items():
        custom = stored.get(name)
        is_custom = bool(isinstance(custom, str) and custom.strip() and custom != default)
        out[name] = {
            "current": custom if is_custom else default,
            "default": default,
            "is_custom": is_custom,
            "required_placeholders": list(REQUIRED_PLACEHOLDERS[name]),
        }
    return out


def _validate_prompt(name: str, body: str) -> None:
    allowed = set(REQUIRED_PLACEHOLDERS[name])
    # Drop doubled (literal) braces before scanning so JSON examples like
    # ``{{"summary": ...}}`` don't register as placeholders.
    scan = body.replace("{{", "").replace("}}", "")
    found = set(_PLACEHOLDER_RE.findall(scan))
    unknown = found - allowed
    if unknown:
        raise ValueError(
            "Unknown placeholder(s): " + ", ".join("{" + u + "}" for u in sorted(unknown))
        )
    missing = allowed - found
    if missing:
        raise ValueError(
            "Missing required placeholder(s): "
            + ", ".join("{" + m + "}" for m in sorted(missing))
        )
    try:
        body.format(**{p: "x" for p in allowed})
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(
            f"Invalid template (literal braces must be doubled as {{{{ }}}}): {exc}"
        ) from exc


def save_prompt(name: str, body: str) -> dict:
    if name not in DEFAULT_PROMPTS:
        raise KeyError(name)
    if not isinstance(body, str) or not body.strip():
        raise ValueError("Prompt body must not be empty")
    _validate_prompt(name, body)
    stored = _read(_PROMPTS_PATH)
    stored[name] = body
    _write(_PROMPTS_PATH, stored)
    return get_prompts()


def reset_prompt(name: str) -> dict:
    if name not in DEFAULT_PROMPTS:
        raise KeyError(name)
    stored = _read(_PROMPTS_PATH)
    stored.pop(name, None)
    _write(_PROMPTS_PATH, stored)
    return get_prompts()
