"""Claude Code CLI implementation of AIProvider.

Mirrors the shape of :class:`OpenCodeAdapter` but shells out to the ``claude``
(Claude Code) binary. Selectable at startup via the ``AI_PROVIDER`` env var
or at runtime from the provider picker.

Most of the body lives in :class:`BaseCLIAIAdapter`; this module only adds
the Claude-Code-specific behaviours that historically blocked everyday use:

1. **Model-name normalisation.** Opencode's UI passes ``provider/model``
   identifiers (``anthropic/claude-sonnet-4-6``); the Claude CLI rejects
   those because the prefix isn't a valid backend marker. We strip the
   prefix here so the *same* model picker works across both providers.
2. **Bedrock model-suggestion surfacing.** When the CLI rejects a
   ``claude-…`` ID and replies "Try ``--model`` to switch to <Bedrock
   profile>", we forward the suggestion as a structured warning the UI
   can promote to a one-click "switch model" tip.
3. **Strict failure handling.** Unlike opencode, a non-zero exit means the
   review failed — we raise so the SSE pipeline emits a top-level error
   event instead of presenting a hollow "0 findings" review to the user.
4. **Read-only flow auto-discovers Skills by default.** Earlier versions
   forced ``--bare`` on read-only runs to dodge a keychain-read step that
   could fall back to a strict model-validation path. But ``--bare`` *also*
   skips auto-discovery of Skills, hooks, plugins, MCP servers and CLAUDE.md
   — which defeats the point of the bundled ``claude_skills/`` (see
   :mod:`app.adapters.ai._skills`). So read-only runs now omit ``--bare`` so
   the connector picks up the project's Skills. Set ``CLAUDE_CODE_BARE=1`` to
   restore the old behaviour on deployments (e.g. some AWS Bedrock setups)
   where the strict model-validation path rejects valid model IDs.
5. **Fix flow uses ``--dangerously-skip-permissions``** but keeps the rest
   of the project context (CLAUDE.md, hooks, plugins, Skills) intact.
"""
from __future__ import annotations

import asyncio  # noqa: F401 — exposed as `claude_code_adapter.asyncio` for tests that monkeypatch `create_subprocess_exec` on this module's namespace.
import logging
import os
import re
from collections.abc import AsyncGenerator

from app.adapters.ai._base import (
    BaseCLIAIAdapter,
    CLIInvocation,
    DEFAULT_FIX_TIMEOUT,
    DEFAULT_GENERATE_TIMEOUT,
    DEFAULT_REVIEW_TIMEOUT,
    Mode,
    StderrCategory,
)
from app.adapters.ai._parsing import parse_review_output as _parse_review_output_impl

logger = logging.getLogger(__name__)

# Parses Claude Code's "Try --model to switch to <id>" suggestion in error
# output. Captures the raw model identifier (alphanumerics, underscores,
# colons, dots, dashes, slashes) so the UI can offer a one-click switch.
_SUGGESTION_RE = re.compile(r"--model to switch to ([\w.:/-]+)")


def _bare_mode_enabled() -> bool:
    """True when ``CLAUDE_CODE_BARE`` opts read-only runs back into ``--bare``.

    ``--bare`` skips Skill / hook / plugin / MCP / CLAUDE.md auto-discovery, so
    it's off by default (the connector wants the bundled Skills). Deployments
    that need the legacy fast / keychain-bypassing path can re-enable it.
    """
    return os.getenv("CLAUDE_CODE_BARE", "").strip().lower() in {"1", "true", "yes"}


def list_models() -> list[str]:
    """Return the model names accepted by the installed claude CLI.

    Returns the universal aliases — ``fable``, ``opus``, ``sonnet``,
    ``haiku`` — which Claude Code maps to the appropriate backend-specific
    model ID automatically, regardless of whether the backend is the
    Anthropic API, AWS Bedrock (any region), Vertex AI, etc.

    Versioned IDs such as ``claude-sonnet-4-6`` are NOT returned because they
    are Anthropic-API-specific and break on Bedrock/Vertex deployments where
    the equivalent ID has a different format (e.g. the Bedrock EU inference
    profile ``eu.anthropic.claude-sonnet-4-5-20250929-v1:0``). Users who
    need a specific pinned ID can enter it via the UI's free-form custom
    input, which round-trips through :func:`normalize_model_name`.
    """
    return ["fable", "opus", "sonnet", "haiku"]


def parse_model_suggestion(text: str) -> str | None:
    """Extract a model ID from a Claude Code "Try --model to switch to X" hint.

    Trailing punctuation (period, comma, semicolon) is stripped so the result
    is safe to round-trip back into a ``--model`` argument.
    """
    match = _SUGGESTION_RE.search(text)
    if not match:
        return None
    return match.group(1).rstrip(".,;")


def normalize_model_name(model: str | None) -> str | None:
    """Translate a model identifier into the form the ``claude`` CLI expects.

    Resolves the single biggest source of "model not found" errors users have
    hit with the Claude Code adapter:

    - ``None`` / empty / whitespace-only → ``None`` (default model).
    - ``"anthropic/claude-sonnet-4-6"`` → ``"claude-sonnet-4-6"`` (opencode
      registry-style prefix stripped — the CLI rejects unknown prefixes).
    - ``"opus"`` / ``"sonnet"`` / ``"haiku"`` → unchanged universal alias.
    - ``"claude-opus-4-7"`` → unchanged versioned Anthropic-API ID.
    - ``"eu.anthropic.claude-sonnet-4-5-20250929-v1:0"`` → unchanged Bedrock
      inference profile (no prefix to strip — there's no slash).
    - ``"  sonnet  "`` → ``"sonnet"`` (whitespace trimmed; otherwise the
      CLI's strict equality check fails).
    """
    if model is None:
        return None
    cleaned = model.strip()
    if not cleaned:
        return None
    # Opencode-style "<registry>/<model>" — strip only the *first* slash, so
    # Bedrock IDs that legitimately contain slashes (none today, but the
    # format reserves them) survive intact.
    if "/" in cleaned:
        cleaned = cleaned.split("/", 1)[1].strip()
        if not cleaned:
            return None
    return cleaned


# Phrases anchored to a model identifier (Claude/Bedrock-style) to avoid
# false positives on unrelated "file may not exist" / "may not have access"
# errors. Each pattern requires either a leading 'model' keyword or a
# trailing model-identifier-shaped token before/after the phrase.
_PHRASE_GROUP = r"(?:may not exist|may not have access|do not have access)"
_ID_TOKEN = r"(?:claude-|anthropic\.|\beu\.|\bus\.|\bap\.)[\w.:-]+"
_BEDROCK_HINT_PATTERNS = (
    re.compile(rf"model\b[^.\n]{{0,80}}\b{_PHRASE_GROUP}", re.IGNORECASE),
    re.compile(rf"{_PHRASE_GROUP}[^.\n]{{0,40}}\bmodel\b", re.IGNORECASE),
    # Anthropic / Bedrock identifier tokens before the phrase.
    re.compile(rf"{_ID_TOKEN}[^.\n]{{0,80}}{_PHRASE_GROUP}", re.IGNORECASE),
    # Mirror: identifier tokens after the phrase.
    re.compile(rf"{_PHRASE_GROUP}[^.\n]{{0,80}}{_ID_TOKEN}", re.IGNORECASE),
)


def _matches_bedrock_hint_phrase(text: str) -> bool:
    return any(p.search(text) for p in _BEDROCK_HINT_PATTERNS)


class ClaudeCodeAdapter(BaseCLIAIAdapter):
    """Implements :class:`AIProvider` using the ``claude`` (Claude Code) CLI."""

    cli_name = "claude-code"
    cli_executable = "claude"
    raise_on_nonzero_exit = True

    def normalize_model(self, model: str | None) -> str | None:
        return normalize_model_name(model)

    def build_invocation(
        self,
        prompt: str,
        *,
        mode: Mode,
        cwd: str | None,
        model: str | None,
    ) -> CLIInvocation:
        # `-p --output-format text` = non-interactive print mode that exits
        # cleanly when stdout drains.
        argv: list[str] = ["claude", "-p", "--output-format", "text"]
        if model:
            argv += ["--model", model]
        if mode == "fix":
            # Fix flow needs CLAUDE.md auto-discovery, hooks, plugins and
            # Skills for project context. Bypass the per-tool prompts so the
            # run isn't blocked waiting for stdin confirmation.
            argv.append("--dangerously-skip-permissions")
        elif _bare_mode_enabled():
            # Opt-in legacy path: --bare bypasses the keychain-read step that
            # could fall back to a strict model-validation path rejecting valid
            # Bedrock IDs. It also skips Skill discovery, so it's off by
            # default and gated behind CLAUDE_CODE_BARE.
            argv.append("--bare")
        # else: read-only runs omit --bare so the connector auto-discovers the
        # bundled Skills (and CLAUDE.md / hooks) like the fix flow does.
        # Always positional. Linux ARG_MAX is ~2 MB so even large reviews fit;
        # `-p` mode does not reliably consume stdin across CLI versions.
        argv.append(prompt)
        return CLIInvocation(argv=argv, cwd=cwd)

    def classify_stderr(self, line: str) -> StderrCategory:
        # Claude Code's stderr is sparse and almost always genuine warnings.
        return "warning"

    def extra_failure_warnings(
        self,
        *,
        rc: int,
        stdout_lines: list[str],
        stderr_lines: list[str],
    ) -> list[str]:
        warnings: list[str] = []
        all_lines = [*stdout_lines, *stderr_lines]
        suggestion: str | None = None
        for line in all_lines:
            suggestion = parse_model_suggestion(line)
            if suggestion:
                break
        if suggestion is not None:
            warnings.append(
                f"[hint] Claude Code suggested model {suggestion!r}. "
                f"Set it from the model picker (free-form custom input) "
                f"or via AI_PROVIDER's UI."
            )
        else:
            joined = " ".join(all_lines)
            if _matches_bedrock_hint_phrase(joined):
                warnings.append(
                    "[hint] Claude Code reports the model is unavailable. On "
                    "Bedrock deployments this often means the ID format is "
                    "Anthropic-direct (e.g. 'claude-sonnet-4-6') instead of "
                    "the inference-profile format your account expects "
                    "(e.g. 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0'). "
                    "Try the universal aliases: opus / sonnet / haiku."
                )
        return warnings

    async def _invoke_stream(
        self,
        message: str,
        *,
        mode: Mode,
        context: str | None = None,
        cwd: str | None = None,
        model: str | None = None,
        timeout: int = DEFAULT_REVIEW_TIMEOUT,
    ) -> AsyncGenerator[str, None]:
        # Route through the module-level helper so existing tests can patch
        # `_stream_claude_code` with a fake stream. The real mode is forwarded
        # so future hooks (logging, before_run, build_invocation) see the
        # caller's intent rather than a flattened "fix"/"generate" reduction.
        async for chunk in _stream_claude_code(
            message,
            context=context,
            cwd=cwd,
            model=model,
            timeout=timeout,
            allow_edits=(mode == "fix"),
            mode=mode,
        ):
            yield chunk


# Singleton shared by the module-level helper to avoid re-instantiating the
# adapter on every invocation.
_SHARED_ADAPTER: ClaudeCodeAdapter | None = None


def _shared_adapter() -> ClaudeCodeAdapter:
    global _SHARED_ADAPTER
    if _SHARED_ADAPTER is None:
        _SHARED_ADAPTER = ClaudeCodeAdapter()
    return _SHARED_ADAPTER


async def _stream_claude_code(
    message: str,
    context: str | None = None,
    timeout: int = DEFAULT_REVIEW_TIMEOUT,
    cwd: str | None = None,
    model: str | None = None,
    allow_edits: bool = False,
    *,
    mode: Mode | None = None,
) -> AsyncGenerator[str, None]:
    """Stream raw ``claude`` stdout and ``\\x00STDERR\\x00``-tagged stderr.

    Kept as a module-level helper because the existing test suite patches
    this name to feed a fake stream into the adapter without spawning a
    real subprocess.

    ``allow_edits`` is the legacy boolean toggle (``True`` enables
    ``--dangerously-skip-permissions`` for the fix flow). New callers should
    pass ``mode`` explicitly so review/analyze/generate stay distinguishable
    in logs and hooks; the boolean is honoured only when ``mode`` is omitted.
    """
    if mode is None:
        mode = "fix" if allow_edits else "generate"
    elif allow_edits and mode != "fix":
        # Defensive: callers should pick one or the other, but `mode` wins.
        logger.warning(
            "claude-code | _stream_claude_code received allow_edits=True with "
            "mode=%r — honouring mode (consider passing only one).",
            mode,
        )
    async for chunk in _shared_adapter().stream_cli(
        message, context=context, cwd=cwd, model=model, timeout=timeout, mode=mode,
    ):
        yield chunk


def _parse_review_output(output: str):
    """Back-compat alias for :func:`app.adapters.ai._parsing.parse_review_output`."""
    return _parse_review_output_impl(output, provider="claude-code")


__all__ = [
    "ClaudeCodeAdapter",
    "DEFAULT_FIX_TIMEOUT",
    "DEFAULT_GENERATE_TIMEOUT",
    "DEFAULT_REVIEW_TIMEOUT",
    "_parse_review_output",
    "_stream_claude_code",
    "list_models",
    "normalize_model_name",
    "parse_model_suggestion",
]
