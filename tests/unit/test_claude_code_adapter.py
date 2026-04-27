"""Tests for the ClaudeCodeAdapter."""
import json

import pytest

from app.adapters.ai import claude_code_adapter
from app.adapters.ai.claude_code_adapter import ClaudeCodeAdapter, _parse_review_output
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
