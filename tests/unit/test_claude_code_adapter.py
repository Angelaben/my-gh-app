"""Tests for the ClaudeCodeAdapter."""
import json

import pytest

from app.adapters.ai import claude_code_adapter
from app.adapters.ai.claude_code_adapter import (
    ClaudeCodeAdapter,
    _parse_review_output,
    _stream_claude_code,
    list_models,
    parse_model_suggestion,
)
from app.domain.exceptions import ProviderError
from app.ports.ai_provider import (
    FixChunkEvent,
    ReviewChunkEvent,
    ReviewResultEvent,
    ReviewWarningEvent,
)


class TestParseReviewOutput:
    def test_parses_valid_json(self):
        raw = json.dumps({
            "summary": "Looks good overall",
            "findings": [
                {
                    "criticality": "P1",
                    "title": "Off-by-one",
                    "description": "Loop bound is wrong",
                    "file": "app/main.py",
                    "line": "42",
                    "suggestion": "Use < not <=",
                }
            ],
        })
        review = _parse_review_output(raw)
        assert review.summary == "Looks good overall"
        assert len(review.findings) == 1
        assert review.findings[0].priority == "P1"
        assert review.findings[0].file == "app/main.py"
        assert review.findings[0].line == 42

    def test_falls_back_on_garbage(self):
        review = _parse_review_output("not json at all")
        assert review.findings == []
        assert review.raw_output == "not json at all"
        assert review.raw_length == len("not json at all")

    def test_extracts_json_with_surrounding_noise(self):
        raw = "Some preamble...\n" + json.dumps({"summary": "ok", "findings": []}) + "\ntrailing"
        review = _parse_review_output(raw)
        assert review.summary == "ok"
        assert review.findings == []


class TestListModels:
    def test_returns_universal_aliases(self):
        assert list_models() == ["fable", "opus", "sonnet", "haiku"]

    def test_does_not_contain_versioned_ids(self):
        # Versioned IDs (e.g. "claude-sonnet-4-6") are backend-specific and
        # break on Bedrock/Vertex deployments — only aliases are returned.
        assert not any("-" in m for m in list_models())


class TestParseModelSuggestion:
    def test_parses_bedrock_eu_suggestion(self):
        line = "API Error (claude-sonnet-4-6): 400 The provided model identifier is invalid.. Try --model to switch to eu.anthropic.claude-sonnet-4-5-20250929-v1:0."
        assert parse_model_suggestion(line) == "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"

    def test_parses_generic_switch_suggestion(self):
        line = "Run --model to pick a different model."
        assert parse_model_suggestion(line) is None

    def test_returns_none_when_no_suggestion(self):
        assert parse_model_suggestion("Authentication error") is None

    def test_strips_trailing_period(self):
        line = "Try --model to switch to us.anthropic.claude-opus-4-20250514-v1:0."
        assert parse_model_suggestion(line) == "us.anthropic.claude-opus-4-20250514-v1:0"


def _async_iter_strings(items):
    async def _gen(*args, **kwargs):
        for x in items:
            yield x
    return _gen


async def test_stream_review_emits_chunks_then_result(monkeypatch):
    payload = json.dumps({"summary": "fine", "findings": []})
    monkeypatch.setattr(
        claude_code_adapter, "_stream_claude_code", _async_iter_strings([payload])
    )
    adapter = ClaudeCodeAdapter()
    events = []
    async for ev in adapter.stream_review("acme/repo", 1, "diff text"):
        events.append(ev)
    assert any(isinstance(e, ReviewChunkEvent) for e in events)
    result_events = [e for e in events if isinstance(e, ReviewResultEvent)]
    assert len(result_events) == 1
    assert result_events[0].review.summary == "fine"


async def test_stream_review_surfaces_stderr_warnings(monkeypatch):
    monkeypatch.setattr(
        claude_code_adapter,
        "_stream_claude_code",
        _async_iter_strings(["{\"summary\":\"x\",\"findings\":[]}", "\x00STDERR\x00boom"]),
    )
    adapter = ClaudeCodeAdapter()
    events = []
    async for ev in adapter.stream_review("acme/repo", 1, "diff"):
        events.append(ev)
    warns = [e for e in events if isinstance(e, ReviewWarningEvent)]
    assert len(warns) == 1
    assert warns[0].lines == ["boom"]


async def test_analyze_comments_returns_empty_for_no_comments():
    adapter = ClaudeCodeAdapter()
    result = await adapter.analyze_comments("acme/repo", 1, [])
    assert result == []


async def test_analyze_comments_parses_json_array(monkeypatch):
    from app.domain.models import Comment
    payload = json.dumps([
        {"author": "alice", "criticality": "P2", "valid": True,
         "interest": "medium", "summary": "ok", "original_body": "hi"}
    ])
    monkeypatch.setattr(
        claude_code_adapter, "_stream_claude_code", _async_iter_strings([payload])
    )
    adapter = ClaudeCodeAdapter()
    result = await adapter.analyze_comments(
        "acme/repo", 1, [Comment(id=1, author="alice", body="hi")]
    )
    assert result == json.loads(payload)


async def test_stream_fix_yields_fix_chunks(monkeypatch):
    monkeypatch.setattr(
        claude_code_adapter, "_stream_claude_code",
        _async_iter_strings(["editing foo.py\n", "done\n"]),
    )
    adapter = ClaudeCodeAdapter()
    chunks = []
    async for ev in adapter.stream_fix("/tmp/wt", "acme/repo", 1, "fix the bug"):
        chunks.append(ev)
    assert all(isinstance(c, FixChunkEvent) for c in chunks)
    assert "".join(c.text for c in chunks) == "editing foo.py\ndone\n"


async def test_generate_text_returns_short_output(monkeypatch):
    monkeypatch.setattr(
        claude_code_adapter, "_stream_claude_code",
        _async_iter_strings(["fix: bump version\n"]),
    )
    adapter = ClaudeCodeAdapter()
    result = await adapter.generate_text("write commit msg")
    assert result == "fix: bump version"


async def test_generate_text_raises_on_oversized_output(monkeypatch):
    monkeypatch.setattr(
        claude_code_adapter, "_stream_claude_code",
        _async_iter_strings(["x" * 1000]),
    )
    adapter = ClaudeCodeAdapter()
    with pytest.raises(ProviderError):
        await adapter.generate_text("anything")


class _FakeStream:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
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


async def test_stream_claude_code_strips_provider_prefix_from_model(monkeypatch):
    captured: dict = {}
    proc = _FakeProc([b'{"summary":"ok","findings":[]}\n'], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    async for _ in _stream_claude_code(
        "short prompt", model="anthropic/claude-sonnet-4-6"
    ):
        pass

    args = captured["args"]
    assert "--model" in args
    assert args[args.index("--model") + 1] == "claude-sonnet-4-6"
    assert "anthropic/claude-sonnet-4-6" not in args


async def test_stream_claude_code_passes_unprefixed_model_through(monkeypatch):
    captured: dict = {}
    proc = _FakeProc([b'{"summary":"ok","findings":[]}\n'], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    async for _ in _stream_claude_code("short prompt", model="claude-opus-4-7"):
        pass

    args = captured["args"]
    assert args[args.index("--model") + 1] == "claude-opus-4-7"


async def test_stream_claude_code_uses_bare_for_read_only_flows(monkeypatch):
    """Review / analyze / generate-text invocations must include --bare so
    Claude Code skips the keychain-read step that fails in headless subprocess
    invocations."""
    captured: dict = {}
    proc = _FakeProc([b'{"summary":"ok","findings":[]}\n'], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    async for _ in _stream_claude_code("hi"):
        pass

    args = captured["args"]
    assert "--bare" in args, f"--bare must be passed for read-only flows, got {args!r}"
    assert "--dangerously-skip-permissions" not in args


async def test_stream_claude_code_omits_bare_for_fix_flow(monkeypatch):
    """The fix flow needs CLAUDE.md / hooks / plugins for project context, so
    --bare must NOT be passed; --dangerously-skip-permissions still is."""
    captured: dict = {}
    proc = _FakeProc([b"ok\n"], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    async for _ in _stream_claude_code("hi", allow_edits=True):
        pass

    args = captured["args"]
    assert "--bare" not in args, f"--bare must be absent for fix flow, got {args!r}"
    assert "--dangerously-skip-permissions" in args


async def test_stream_claude_code_warns_then_raises_on_nonzero_exit_with_output(monkeypatch):
    """rc != 0 must yield warnings (so the UI's ⚠ panel can show them) AND
    then raise ProviderError so the SSE pipeline emits a top-level `error`
    event the user can see."""
    from app.domain.exceptions import ProviderError

    captured: dict = {}
    error_msg = b"There's an issue with the selected model (foo). It may not exist...\n"
    proc = _FakeProc([error_msg], [], 1)
    _patch_subprocess(monkeypatch, proc, captured)

    chunks = []
    with pytest.raises(ProviderError, match="claude exited with code 1"):
        async for chunk in _stream_claude_code("short prompt"):
            chunks.append(chunk)

    stderr_chunks = [c for c in chunks if c.startswith("\x00STDERR\x00")]
    assert any("exited with code 1" in c for c in stderr_chunks), (
        f"expected a non-zero-exit warning to be yielded before the raise, got {chunks!r}"
    )
    # The captured stdout should also be promoted into the warning channel.
    assert any("issue with the selected model" in c for c in stderr_chunks), (
        f"expected stdout content to be surfaced as a warning, got {stderr_chunks!r}"
    )


async def test_stream_claude_code_raises_on_nonzero_exit_with_no_output(monkeypatch):
    """rc != 0 with empty stdout still raises so the UI shows the failure."""
    from app.domain.exceptions import ProviderError

    captured: dict = {}
    proc = _FakeProc([], [b"some stderr complaint\n"], 1)
    _patch_subprocess(monkeypatch, proc, captured)

    with pytest.raises(ProviderError, match="claude exited with code 1"):
        async for _ in _stream_claude_code("short prompt"):
            pass


async def test_stream_claude_code_always_uses_positional_arg(monkeypatch):
    """Large prompts must still be passed as a positional argument — claude's
    `-p` mode does not reliably consume stdin, so we never use the stdin path."""
    captured: dict = {}
    proc = _FakeProc([b'{"summary":"ok","findings":[]}\n'], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    big_prompt = "x" * 50_000  # well over the previous 4 KB stdin threshold
    async for _ in _stream_claude_code(big_prompt):
        pass

    # The prompt should appear as the last positional argument.
    args = captured["args"]
    assert args[-1] == big_prompt
    # And we should NOT have asked for a stdin pipe.
    assert "stdin" not in captured["kwargs"]
