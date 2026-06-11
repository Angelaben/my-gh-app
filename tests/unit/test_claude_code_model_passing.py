"""Tests for the Claude Code model-passing path.

This is the historical big-blocker for the connector: the wrong identifier
silently rejected by the CLI, errors that the user can't act on, and no
hint about the Bedrock format mismatch. These tests pin down the contract
:func:`normalize_model_name` and the failure-warning surface have to honour.
"""
from __future__ import annotations

import asyncio

import pytest

from app.adapters.ai import claude_code_adapter
from app.adapters.ai._base import STDERR_MARKER
from app.adapters.ai.claude_code_adapter import (
    ClaudeCodeAdapter,
    _stream_claude_code,
    list_models,
    normalize_model_name,
    parse_model_suggestion,
)
from app.domain.exceptions import ProviderError


# ---- normalize_model_name --------------------------------------------------


class TestNormalizeModelName:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, None),
            ("", None),
            ("   ", None),
            # Universal aliases pass through.
            ("opus", "opus"),
            ("sonnet", "sonnet"),
            ("haiku", "haiku"),
            ("  sonnet  ", "sonnet"),
            # Versioned Anthropic-direct IDs pass through.
            ("claude-opus-4-7", "claude-opus-4-7"),
            ("claude-sonnet-4-6", "claude-sonnet-4-6"),
            ("claude-haiku-4-5-20251001", "claude-haiku-4-5-20251001"),
            # Opencode registry-style prefix is stripped.
            ("anthropic/claude-sonnet-4-6", "claude-sonnet-4-6"),
            ("openai/gpt-4o", "gpt-4o"),
            ("openrouter/foo/bar", "foo/bar"),
            # Bedrock inference profiles must NOT be touched (they have no slash).
            (
                "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
            ),
            (
                "us.anthropic.claude-opus-4-20250514-v1:0",
                "us.anthropic.claude-opus-4-20250514-v1:0",
            ),
            # Bedrock model IDs without the regional prefix.
            (
                "anthropic.claude-3-5-sonnet-20241022-v2:0",
                "anthropic.claude-3-5-sonnet-20241022-v2:0",
            ),
            # Trailing whitespace after stripping the prefix.
            ("anthropic/  claude-opus-4-7  ", "claude-opus-4-7"),
        ],
    )
    def test_canonical_table(self, raw, expected):
        assert normalize_model_name(raw) == expected

    def test_empty_after_prefix_strip_returns_none(self):
        assert normalize_model_name("anthropic/") is None
        assert normalize_model_name("anthropic/   ") is None


# ---- list_models -----------------------------------------------------------


class TestListModels:
    def test_returns_universal_aliases(self):
        assert list_models() == ["fable", "opus", "sonnet", "haiku"]


# ---- parse_model_suggestion -----------------------------------------------


class TestParseModelSuggestion:
    def test_parses_bedrock_eu_suggestion(self):
        line = (
            "API Error (claude-sonnet-4-6): 400 The provided model identifier "
            "is invalid.. Try --model to switch to "
            "eu.anthropic.claude-sonnet-4-5-20250929-v1:0."
        )
        assert parse_model_suggestion(line) == (
            "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
        )

    def test_strips_trailing_punctuation(self):
        assert (
            parse_model_suggestion(
                "Try --model to switch to us.anthropic.claude-opus-4-20250514-v1:0,"
            )
            == "us.anthropic.claude-opus-4-20250514-v1:0"
        )

    def test_returns_none_when_no_suggestion(self):
        assert parse_model_suggestion("some unrelated error") is None

    def test_does_not_match_generic_phrasing(self):
        assert parse_model_suggestion("Run --model to pick another") is None


# ---- model-suggestion warning surface (the big fix) ------------------------


class _FakeStream:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        # Mirror the test_base_cli_adapter helper: yield to the event loop
        # so concurrent helper tasks (stdin drainer, stderr reader) get
        # scheduled between lines exactly like a real I/O reader would.
        await asyncio.sleep(0)
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeProc:
    def __init__(self, stdout_lines, stderr_lines, returncode):
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = _FakeStream(stderr_lines)
        self.returncode = returncode

    async def wait(self):
        return self.returncode

    def kill(self):
        pass


def _patch_subprocess(monkeypatch, proc, captured):
    async def fake_create(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(
        claude_code_adapter.asyncio, "create_subprocess_exec", fake_create
    )


async def test_failure_with_bedrock_suggestion_emits_actionable_hint(monkeypatch):
    """When the CLI fails with a "Try --model to switch to X" suggestion in
    stdout, a structured ``[hint]`` warning naming the suggested model is
    surfaced so the UI can promote it to a one-click switch."""
    captured: dict = {}
    suggestion_line = (
        b"API Error (claude-sonnet-4-6): 400 The provided model identifier is "
        b"invalid.. Try --model to switch to "
        b"eu.anthropic.claude-sonnet-4-5-20250929-v1:0.\n"
    )
    proc = _FakeProc([suggestion_line], [], 1)
    _patch_subprocess(monkeypatch, proc, captured)

    chunks = []
    with pytest.raises(ProviderError):
        async for chunk in _stream_claude_code(
            "review please", model="anthropic/claude-sonnet-4-6"
        ):
            chunks.append(chunk)

    stderr_chunks = [
        c[len(STDERR_MARKER):] for c in chunks if c.startswith(STDERR_MARKER)
    ]
    hints = [c for c in stderr_chunks if c.startswith("[hint]")]
    assert hints, f"Expected at least one [hint] warning, got {stderr_chunks!r}"
    assert "eu.anthropic.claude-sonnet-4-5-20250929-v1:0" in hints[0]


async def test_failure_with_bedrock_phrasing_emits_format_hint(monkeypatch):
    """When the CLI says "may not exist" without a suggestion, we still nudge
    the user toward the inference-profile format vs. universal aliases."""
    captured: dict = {}
    proc = _FakeProc(
        [b"Error: model 'foo' may not exist or you may not have access\n"],
        [],
        1,
    )
    _patch_subprocess(monkeypatch, proc, captured)

    chunks = []
    with pytest.raises(ProviderError):
        async for chunk in _stream_claude_code("hi", model="anthropic/foo"):
            chunks.append(chunk)

    stderr_chunks = [
        c[len(STDERR_MARKER):] for c in chunks if c.startswith(STDERR_MARKER)
    ]
    hints = [c for c in stderr_chunks if c.startswith("[hint]")]
    assert hints
    flat = " ".join(hints)
    assert "Bedrock" in flat
    assert "eu.anthropic" in flat
    assert "opus" in flat


async def test_success_does_not_emit_failure_hints(monkeypatch):
    """Successful runs must not emit the failure hints, even if the stdout
    happens to contain Bedrock-suggestion-shaped phrasing."""
    captured: dict = {}
    proc = _FakeProc(
        [b'{"summary":"ok","findings":[]}\n'],
        [],
        0,
    )
    _patch_subprocess(monkeypatch, proc, captured)

    chunks = []
    async for chunk in _stream_claude_code("hi"):
        chunks.append(chunk)

    stderr_chunks = [
        c[len(STDERR_MARKER):] for c in chunks if c.startswith(STDERR_MARKER)
    ]
    hints = [c for c in stderr_chunks if c.startswith("[hint]")]
    assert hints == []


async def test_normalized_model_appears_in_argv(monkeypatch):
    """End-to-end: the prefix-stripped model is what the CLI receives."""
    captured: dict = {}
    proc = _FakeProc([b'{"summary":"ok","findings":[]}\n'], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    async for _ in _stream_claude_code(
        "hi", model="anthropic/claude-sonnet-4-6"
    ):
        pass

    args = captured["args"]
    assert "--model" in args
    assert args[args.index("--model") + 1] == "claude-sonnet-4-6"
    # The opencode-style prefixed form must NOT reach the CLI.
    assert "anthropic/claude-sonnet-4-6" not in args


async def test_blank_or_whitespace_model_is_omitted(monkeypatch):
    """A blank model string is treated like ``None`` — no ``--model`` flag.

    Without this, a user clearing the model picker would send ``--model ''``
    to the CLI and trigger the strict validation path.
    """
    captured: dict = {}
    proc = _FakeProc([b'{"summary":"ok","findings":[]}\n'], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    async for _ in _stream_claude_code("hi", model="   "):
        pass

    args = captured["args"]
    assert "--model" not in args


async def test_bedrock_inference_profile_passes_through(monkeypatch):
    """Bedrock inference-profile IDs (e.g. ``eu.anthropic.…``) must reach
    the CLI unchanged — they don't have a provider-prefix slash to strip."""
    captured: dict = {}
    proc = _FakeProc([b'{"summary":"ok","findings":[]}\n'], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    bedrock_id = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
    async for _ in _stream_claude_code("hi", model=bedrock_id):
        pass

    args = captured["args"]
    assert args[args.index("--model") + 1] == bedrock_id


async def test_extra_failure_warnings_unit():
    """The hook itself is exposed for unit tests independent of subprocess."""
    adapter = ClaudeCodeAdapter()
    warnings = adapter.extra_failure_warnings(
        rc=1,
        stdout_lines=["Try --model to switch to eu.anthropic.foo-bar.\n"],
        stderr_lines=[],
    )
    assert warnings
    assert "eu.anthropic.foo-bar" in warnings[0]


async def test_extra_failure_warnings_empty_when_no_signal():
    adapter = ClaudeCodeAdapter()
    assert (
        adapter.extra_failure_warnings(
            rc=1, stdout_lines=["unrelated error"], stderr_lines=[]
        )
        == []
    )


# ---- argv assembly: --bare vs --dangerously-skip-permissions --------------


async def test_argv_includes_bare_for_review(monkeypatch):
    """Read-only flows (review/analyze/generate) must add ``--bare`` so the
    keychain bypass doesn't fall back to the strict model-validation path.
    """
    captured: dict = {}
    proc = _FakeProc([b'{"summary":"ok","findings":[]}\n'], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    async for _ in _stream_claude_code("hi", mode="review"):
        pass

    args = captured["args"]
    assert "--bare" in args, f"--bare must be present for read-only flows: {args!r}"
    assert "--dangerously-skip-permissions" not in args


async def test_argv_includes_skip_permissions_for_fix(monkeypatch):
    """Fix flow must include ``--dangerously-skip-permissions`` (and NOT
    ``--bare`` — the fix flow needs CLAUDE.md / hooks / plugins for context)."""
    captured: dict = {}
    proc = _FakeProc([b"editing\n"], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    async for _ in _stream_claude_code("hi", mode="fix"):
        pass

    args = captured["args"]
    assert "--dangerously-skip-permissions" in args
    assert "--bare" not in args, (
        f"--bare must be ABSENT for fix flow (it suppresses CLAUDE.md): {args!r}"
    )


async def test_argv_skip_permissions_via_legacy_allow_edits(monkeypatch):
    """The legacy ``allow_edits=True`` toggle still routes to the fix path
    when ``mode`` is omitted (back-compat for any caller still on the old
    keyword)."""
    captured: dict = {}
    proc = _FakeProc([b"editing\n"], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    async for _ in _stream_claude_code("hi", allow_edits=True):
        pass

    args = captured["args"]
    assert "--dangerously-skip-permissions" in args


async def test_argv_first_three_args_are_p_text(monkeypatch):
    """``-p --output-format text`` is the non-interactive print mode that
    exits when stdout drains. Without it the CLI hangs waiting for input."""
    captured: dict = {}
    proc = _FakeProc([b"ok\n"], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    async for _ in _stream_claude_code("hi", mode="review"):
        pass

    args = captured["args"]
    assert args[0] == "claude"
    assert args[1] == "-p"
    assert "--output-format" in args
    assert args[args.index("--output-format") + 1] == "text"


# ---- Bedrock-phrase precision regression ----------------------------------


async def test_unrelated_may_not_exist_does_not_emit_bedrock_hint(monkeypatch):
    """The Bedrock format hint is only useful when the failure is about a
    model identifier — generic "file may not exist" must NOT trigger it."""
    captured: dict = {}
    proc = _FakeProc(
        [b"Error: the configuration file may not exist on disk\n"],
        [],
        1,
    )
    _patch_subprocess(monkeypatch, proc, captured)

    chunks = []
    with pytest.raises(ProviderError):
        async for chunk in _stream_claude_code("hi"):
            chunks.append(chunk)

    stderr_chunks = [
        c[len(STDERR_MARKER):] for c in chunks if c.startswith(STDERR_MARKER)
    ]
    bedrock_hints = [c for c in stderr_chunks if "Bedrock" in c]
    assert bedrock_hints == [], (
        f"Bedrock hint must not fire on unrelated 'may not exist' messages: "
        f"{bedrock_hints!r}"
    )


async def test_model_centred_may_not_exist_does_emit_bedrock_hint():
    """The model-centred phrasing should still trigger the hint."""
    adapter = ClaudeCodeAdapter()
    warnings = adapter.extra_failure_warnings(
        rc=1,
        stdout_lines=["Error: model 'claude-foo-bar' may not exist or you may not have access"],
        stderr_lines=[],
    )
    assert warnings, "model-centred 'may not exist' must trigger the hint"
    assert "Bedrock" in warnings[0]


async def test_anthropic_id_phrasing_emits_bedrock_hint():
    """An Anthropic-direct ID near "may not exist" should trigger the hint
    even when the word 'model' isn't on the same line."""
    adapter = ClaudeCodeAdapter()
    warnings = adapter.extra_failure_warnings(
        rc=1,
        stdout_lines=["claude-sonnet-4-6 may not exist or you may not have access"],
        stderr_lines=[],
    )
    assert warnings


async def test_phrase_then_id_emits_bedrock_hint():
    """Mirror pattern: "may not exist for claude-foo" — id appears AFTER the
    phrase. Both word orders are equally common in CLI error messages."""
    adapter = ClaudeCodeAdapter()
    warnings = adapter.extra_failure_warnings(
        rc=1,
        stdout_lines=["may not have access to claude-opus-4-7"],
        stderr_lines=[],
    )
    assert warnings, "id-after-phrase phrasing must trigger the Bedrock hint"


# ---- mode forwarding regression -------------------------------------------


async def test_mode_kwarg_forwarded_to_stream_cli(monkeypatch):
    """The ``mode`` kwarg must reach the underlying stream_cli call so
    logging and adapter hooks see the actual caller intent (review /
    analyze) instead of always being collapsed to "generate".
    """
    captured_mode: dict[str, str] = {}

    async def fake_stream_cli(self, message, *, mode, context=None, cwd=None, model=None, timeout=300):
        captured_mode["mode"] = mode
        if False:
            yield  # pragma: no cover

    monkeypatch.setattr(ClaudeCodeAdapter, "stream_cli", fake_stream_cli)

    async for _ in _stream_claude_code("hi", mode="analyze"):
        pass

    assert captured_mode["mode"] == "analyze"
