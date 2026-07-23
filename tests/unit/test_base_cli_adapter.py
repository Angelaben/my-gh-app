"""Tests for the shared :mod:`app.adapters.ai._base` infrastructure.

These tests exercise behaviour that's identical for every CLI-based adapter
through a tiny fake adapter so we don't need a live ``claude`` or ``opencode``
binary on PATH. The concrete adapters then have lighter unit tests focused
on their own quirks (model normalization, stderr classification, etc.).
"""
from __future__ import annotations

import asyncio

import pytest

from app.adapters.ai import _base
from app.adapters.ai._base import (
    BaseCLIAIAdapter,
    CLIInvocation,
    PROGRESS_MARKER,
    STDERR_MARKER,
    split_stderr_chunk,
)
from app.adapters.ai._parsing import parse_analyze_output, parse_review_output
from app.domain.exceptions import ProviderError
from app.domain.models import Comment
from app.ports.ai_provider import (
    ReviewChunkEvent,
    ReviewProgressEvent,
    ReviewResultEvent,
    ReviewWarningEvent,
)


# ---- fake CLI process plumbing ---------------------------------------------


class _FakeStream:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        # Yield to the event loop so that concurrent helper tasks
        # (stderr reader, stdin drainer) get a chance to run between
        # each line — real I/O readers always do.
        await asyncio.sleep(0)
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeProc:
    def __init__(
        self,
        stdout_lines: list[bytes],
        stderr_lines: list[bytes],
        returncode: int,
    ) -> None:
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = _FakeStream(stderr_lines)
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:  # pragma: no cover — only triggered on timeout
        pass


def _patch_subprocess(monkeypatch, proc: _FakeProc, captured: dict) -> None:
    async def fake_create(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(_base.asyncio, "create_subprocess_exec", fake_create)


class _FakeAdapter(BaseCLIAIAdapter):
    """Minimal adapter used to assert behaviour on the base class."""

    cli_name = "fake"
    cli_executable = "fake"
    raise_on_nonzero_exit = False

    def __init__(
        self,
        *,
        argv_extra: list[str] | None = None,
        stdin_payload: str | None = None,
        cwd_in_invocation: str | None = None,
        classify=None,
        extra_warning_lines: list[str] | None = None,
    ) -> None:
        self._argv_extra = argv_extra or []
        self._stdin_payload = stdin_payload
        self._cwd_in_invocation = cwd_in_invocation
        self._classify_override = classify
        self._extra_warnings = extra_warning_lines or []
        self.before_run_calls: list[tuple[str | None, str]] = []

    def build_invocation(self, prompt, *, mode, cwd, model):
        argv = ["fake", *self._argv_extra]
        if model:
            argv += ["--model", model]
        argv.append(prompt)
        return CLIInvocation(
            argv=argv,
            cwd=self._cwd_in_invocation if self._cwd_in_invocation else cwd,
            stdin_payload=self._stdin_payload,
        )

    def classify_stderr(self, line):
        if self._classify_override:
            return self._classify_override(line)
        return super().classify_stderr(line)

    def extra_failure_warnings(self, *, rc, stdout_lines, stderr_lines):
        return list(self._extra_warnings)

    def before_run(self, *, cwd, mode):
        self.before_run_calls.append((cwd, mode))


# ---- CLIInvocation + helpers -----------------------------------------------


class TestCLIInvocation:
    def test_default_fields(self):
        inv = CLIInvocation(argv=["x"])
        assert inv.argv == ["x"]
        assert inv.cwd is None
        assert inv.stdin_payload is None
        assert inv.extra_env == {}


class TestSplitStderrChunk:
    def test_recognises_marker(self):
        is_err, payload = split_stderr_chunk(f"{STDERR_MARKER}boom")
        assert is_err is True
        assert payload == "boom"

    def test_passes_through_regular_chunks(self):
        is_err, payload = split_stderr_chunk("hello\n")
        assert is_err is False
        assert payload == "hello\n"


# ---- streaming loop --------------------------------------------------------


async def test_stream_cli_yields_stdout_then_stderr_marker(monkeypatch):
    captured: dict = {}
    proc = _FakeProc(
        stdout_lines=[b"line1\n", b"line2\n"],
        stderr_lines=[b"warn1\n"],
        returncode=0,
    )
    _patch_subprocess(monkeypatch, proc, captured)

    adapter = _FakeAdapter()
    chunks = []
    async for chunk in adapter.stream_cli("hi", mode="generate"):
        chunks.append(chunk)

    stdout_chunks = [c for c in chunks if not c.startswith(STDERR_MARKER)]
    stderr_chunks = [c[len(STDERR_MARKER):] for c in chunks if c.startswith(STDERR_MARKER)]
    assert stdout_chunks == ["line1\n", "line2\n"]
    assert stderr_chunks == ["warn1"]


class _FakeStdin:
    """Mimics ``asyncio.StreamWriter``'s minimum surface used by ``_drain_stdin``."""

    def __init__(self, raise_on_drain: BaseException | None = None):
        self.written: bytes = b""
        self.closed = False
        self.drained = False
        self._raise_on_drain = raise_on_drain

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        self.drained = True
        if self._raise_on_drain is not None:
            raise self._raise_on_drain

    def close(self) -> None:
        self.closed = True


async def test_stream_cli_passes_prompt_via_stdin_when_payload_set(monkeypatch):
    """Large prompt path: stdin pipe requested, payload written, drain awaited."""
    captured: dict = {}
    proc = _FakeProc([b""], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    class _StdinAdapter(_FakeAdapter):
        def build_invocation(self, prompt, *, mode, cwd, model):
            return CLIInvocation(argv=["fake"], stdin_payload=prompt)

    # Pre-attach a fake stdin to the FakeProc; the base class will pick it up
    # because the invocation declares stdin_payload != None and our fake
    # exposes a writable+drainable interface.
    proc.stdin = _FakeStdin()  # type: ignore[attr-defined]

    adapter = _StdinAdapter()
    async for _ in adapter.stream_cli("the-prompt", mode="generate"):
        pass

    args = captured["args"]
    assert "the-prompt" not in args, "prompt must not also appear as a positional arg"
    assert "stdin" in captured["kwargs"], "stdin pipe must be requested"
    assert proc.stdin.written == b"the-prompt"  # type: ignore[attr-defined]
    assert proc.stdin.drained is True, (  # type: ignore[attr-defined]
        "drain() must be awaited so >64 KB prompts can't deadlock the event loop"
    )
    assert proc.stdin.closed is True  # type: ignore[attr-defined]


async def test_stream_cli_tolerates_broken_pipe_on_stdin(monkeypatch):
    """A child that exits before consuming stdin must not crash the run."""
    captured: dict = {}
    proc = _FakeProc([b"out\n"], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    class _StdinAdapter(_FakeAdapter):
        def build_invocation(self, prompt, *, mode, cwd, model):
            return CLIInvocation(argv=["fake"], stdin_payload=prompt)

    proc.stdin = _FakeStdin(raise_on_drain=BrokenPipeError())  # type: ignore[attr-defined]

    adapter = _StdinAdapter()
    chunks = []
    async for chunk in adapter.stream_cli("payload", mode="generate"):
        chunks.append(chunk)

    # Run must complete normally and yield the stdout we got.
    assert any(c == "out\n" for c in chunks)
    assert proc.stdin.closed is True  # type: ignore[attr-defined]


async def test_stream_cli_classify_stderr_skip_silences_line(monkeypatch):
    captured: dict = {}
    proc = _FakeProc(
        stdout_lines=[b"ok\n"],
        stderr_lines=[b"please-skip\n", b"please-warn\n"],
        returncode=0,
    )
    _patch_subprocess(monkeypatch, proc, captured)

    def classify(line):
        return "skip" if "please-skip" in line else "warning"

    adapter = _FakeAdapter(classify=classify)
    chunks = []
    async for chunk in adapter.stream_cli("hi", mode="generate"):
        chunks.append(chunk)

    stderr_payloads = [c[len(STDERR_MARKER):] for c in chunks if c.startswith(STDERR_MARKER)]
    assert "please-warn" in stderr_payloads
    assert all("please-skip" not in p for p in stderr_payloads)


async def test_stream_cli_classify_stderr_info_logs_at_info_level(monkeypatch, caplog):
    captured: dict = {}
    proc = _FakeProc(
        stdout_lines=[b"ok\n"],
        stderr_lines=[b"> progress\n"],
        returncode=0,
    )
    _patch_subprocess(monkeypatch, proc, captured)

    def classify(line):
        return "info"

    adapter = _FakeAdapter(classify=classify)
    chunks = []
    with caplog.at_level("INFO", logger="fake"):
        async for chunk in adapter.stream_cli("hi", mode="generate"):
            chunks.append(chunk)

    # Info-classified lines must NOT show up in the warning channel.
    assert not any("> progress" in r.message for r in caplog.records if r.levelname == "WARNING")
    # Info-classified lines MUST be emitted as PROGRESS_MARKER chunks in real time.
    progress_payloads = [c[len(PROGRESS_MARKER):] for c in chunks if c.startswith(PROGRESS_MARKER)]
    assert any("> progress" in p for p in progress_payloads)


async def test_stream_cli_classify_stderr_info_emits_progress_marker(monkeypatch):
    """Info-classified stderr lines are yielded as PROGRESS_MARKER chunks and
    must NOT appear in the STDERR_MARKER (warning) channel."""
    captured: dict = {}
    proc = _FakeProc(
        stdout_lines=[b"line1\n"],
        stderr_lines=[b"> step one\n", "✓ done\n".encode()],
        returncode=0,
    )
    _patch_subprocess(monkeypatch, proc, captured)

    def classify(line):
        return "info"

    adapter = _FakeAdapter(classify=classify)
    chunks = []
    async for chunk in adapter.stream_cli("hi", mode="generate"):
        chunks.append(chunk)

    progress = [c[len(PROGRESS_MARKER):] for c in chunks if c.startswith(PROGRESS_MARKER)]
    assert "> step one" in progress
    assert "✓ done" in progress

    # Must NOT appear as warnings
    warnings = [c[len(STDERR_MARKER):] for c in chunks if c.startswith(STDERR_MARKER)]
    assert all("> step one" not in w and "✓ done" not in w for w in warnings)


async def test_stream_cli_raises_on_nonzero_exit_with_no_stdout_even_when_lenient(monkeypatch):
    """A crash that produces zero stdout has nothing salvageable — leniency
    (raise_on_nonzero_exit=False) must not downgrade it to a silent, hollow
    "0 findings" success. The warning lines are still emitted first so the
    caller can see what happened."""
    captured: dict = {}
    proc = _FakeProc([], [b"some failure\n"], 2)
    _patch_subprocess(monkeypatch, proc, captured)

    adapter = _FakeAdapter()  # raise_on_nonzero_exit=False by default
    chunks = []
    with pytest.raises(ProviderError, match="fake exited with code 2"):
        async for chunk in adapter.stream_cli("hi", mode="generate"):
            chunks.append(chunk)

    stderr_payloads = [c[len(STDERR_MARKER):] for c in chunks if c.startswith(STDERR_MARKER)]
    assert any("exited with code 2 and produced no output" in p for p in stderr_payloads)
    assert any("some failure" in p for p in stderr_payloads)


async def test_stream_cli_includes_extra_failure_warnings_on_nonzero_exit(monkeypatch):
    captured: dict = {}
    proc = _FakeProc([b"first stdout\n"], [b"first stderr\n"], 1)
    _patch_subprocess(monkeypatch, proc, captured)

    adapter = _FakeAdapter(extra_warning_lines=["[hint] try X"])
    chunks = []
    async for chunk in adapter.stream_cli("hi", mode="generate"):
        chunks.append(chunk)

    stderr_payloads = [c[len(STDERR_MARKER):] for c in chunks if c.startswith(STDERR_MARKER)]
    assert any("[hint] try X" in p for p in stderr_payloads)


async def test_stream_cli_extra_failure_warnings_skipped_on_success(monkeypatch):
    """Extra warnings exist to enrich failure reporting — they MUST NOT fire
    on a successful exit, even if the adapter naively returns them."""
    captured: dict = {}
    proc = _FakeProc([b"all good\n"], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    adapter = _FakeAdapter(extra_warning_lines=["[hint] should not fire"])
    chunks = []
    async for chunk in adapter.stream_cli("hi", mode="generate"):
        chunks.append(chunk)

    stderr_payloads = [c[len(STDERR_MARKER):] for c in chunks if c.startswith(STDERR_MARKER)]
    assert all("should not fire" not in p for p in stderr_payloads)


async def test_stream_cli_raises_provider_error_when_strict(monkeypatch):
    captured: dict = {}
    proc = _FakeProc([b"first line\n"], [b"second line\n"], 1)
    _patch_subprocess(monkeypatch, proc, captured)

    class _StrictAdapter(_FakeAdapter):
        raise_on_nonzero_exit = True

    adapter = _StrictAdapter()
    with pytest.raises(ProviderError, match="fake exited with code 1"):
        async for _ in adapter.stream_cli("hi", mode="generate"):
            pass


async def test_stream_cli_lenient_adapter_does_not_raise(monkeypatch):
    captured: dict = {}
    proc = _FakeProc([b"output\n"], [], 1)
    _patch_subprocess(monkeypatch, proc, captured)

    adapter = _FakeAdapter()  # raise_on_nonzero_exit=False by default
    # Must not raise even when rc != 0.
    async for _ in adapter.stream_cli("hi", mode="generate"):
        pass


async def test_stream_cli_calls_before_run_with_mode_and_cwd(monkeypatch):
    captured: dict = {}
    proc = _FakeProc([b"ok\n"], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    adapter = _FakeAdapter()
    async for _ in adapter.stream_cli("hi", mode="fix", cwd="/tmp/wt"):
        pass

    assert adapter.before_run_calls == [("/tmp/wt", "fix")]


async def test_stream_cli_concatenates_message_and_context(monkeypatch):
    captured: dict = {}
    proc = _FakeProc([b"ok\n"], [], 0)
    _patch_subprocess(monkeypatch, proc, captured)

    adapter = _FakeAdapter()
    async for _ in adapter.stream_cli("the message", context="DIFF", mode="review"):
        pass

    final_arg = captured["args"][-1]
    assert final_arg.startswith("the message")
    assert final_arg.endswith("DIFF")
    assert "---" in final_arg


# ---- cleanup on cancellation / mid-stream close ---------------------------


class _BlockingProc:
    """Subprocess fake whose stdout never returns — used to verify the
    cleanup path kills the child when the consumer disconnects."""

    def __init__(self):
        self.stdout = self
        self.stderr = self
        self.returncode: int | None = None
        self.killed = False
        self._wait_event = asyncio.Event()

    async def readline(self) -> bytes:
        # Wait forever — the only way out is a kill().
        await asyncio.Event().wait()
        return b""

    async def wait(self) -> int:
        await self._wait_event.wait()
        return self.returncode if self.returncode is not None else -9

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._wait_event.set()


async def test_stream_cli_kills_subprocess_when_consumer_disconnects(monkeypatch):
    """If the generator is closed mid-stream (consumer disconnects, request
    cancelled), the cleanup path MUST kill the subprocess so we don't leak
    a long-running ``claude``/``opencode`` process per dropped request."""
    captured: dict = {}
    proc = _BlockingProc()

    async def fake_create(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(_base.asyncio, "create_subprocess_exec", fake_create)

    adapter = _FakeAdapter()
    gen = adapter.stream_cli("hi", mode="generate", timeout=600)

    # Pull a single chunk — the BlockingProc readline hangs, so this races
    # against the close below.
    pull_task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)  # let the create_task / spawn settle

    # Cancel the pull and then close the generator — this is what asyncio
    # does when the request handler is cancelled.
    pull_task.cancel()
    try:
        await pull_task
    except (asyncio.CancelledError, Exception):
        pass
    await gen.aclose()

    assert proc.killed, "subprocess must be killed when generator is closed"


# ---- AIProvider methods on the base ---------------------------------------


def _async_iter(items):
    async def _gen(*args, **kwargs):
        for it in items:
            yield it
    return _gen


async def test_stream_review_yields_chunks_then_result(monkeypatch):
    adapter = _FakeAdapter()
    payload = '{"summary": "ok", "findings": []}'
    monkeypatch.setattr(adapter, "_invoke_stream", _async_iter([payload]))

    events = []
    async for ev in adapter.stream_review("acme/repo", 1, "diff"):
        events.append(ev)

    assert any(isinstance(e, ReviewChunkEvent) for e in events)
    results = [e for e in events if isinstance(e, ReviewResultEvent)]
    assert len(results) == 1
    assert results[0].review.summary == "ok"


async def test_stream_synthesis_yields_chunks_then_result(monkeypatch):
    adapter = _FakeAdapter()
    payload = '{"summary": "consolidated", "findings": [{"criticality": "P1", "title": "t", "description": "d"}]}'
    captured: dict = {}

    async def fake_invoke(message, **kwargs):
        captured["message"] = message
        captured["context"] = kwargs.get("context")
        yield payload

    monkeypatch.setattr(adapter, "_invoke_stream", fake_invoke)

    events = []
    async for ev in adapter.stream_synthesis("acme/repo", 7, "candidate findings…"):
        events.append(ev)

    results = [e for e in events if isinstance(e, ReviewResultEvent)]
    assert len(results) == 1
    assert results[0].review.summary == "consolidated"
    assert [f.priority for f in results[0].review.findings] == ["P1"]
    # The synthesis prompt (not the review prompt) is used, rendered with the
    # PR coordinates, and the candidate payload rides in as the context.
    assert "consolidating the code review" in captured["message"]
    assert "#7" in captured["message"] and "acme/repo" in captured["message"]
    assert captured["context"] == "candidate findings…"


async def test_stream_review_emits_warning_event_for_stderr_chunks(monkeypatch):
    adapter = _FakeAdapter()
    monkeypatch.setattr(
        adapter,
        "_invoke_stream",
        _async_iter([
            '{"summary":"x","findings":[]}',
            f"{STDERR_MARKER}boom",
            f"{STDERR_MARKER}again",
        ]),
    )

    events = []
    async for ev in adapter.stream_review("acme/repo", 1, "diff"):
        events.append(ev)

    warns = [e for e in events if isinstance(e, ReviewWarningEvent)]
    assert len(warns) == 1
    assert warns[0].lines == ["boom", "again"]


async def test_stream_review_emits_progress_event_for_progress_chunks(monkeypatch):
    """PROGRESS_MARKER chunks from the stream become ReviewProgressEvent objects
    and must NOT appear as chunks or warnings."""
    adapter = _FakeAdapter()
    monkeypatch.setattr(
        adapter,
        "_invoke_stream",
        _async_iter([
            f"{PROGRESS_MARKER}> Reading context",
            f"{PROGRESS_MARKER}✓ Tool use complete",
            '{"summary":"ok","findings":[]}',
        ]),
    )

    events = []
    async for ev in adapter.stream_review("acme/repo", 1, "diff"):
        events.append(ev)

    progress = [e for e in events if isinstance(e, ReviewProgressEvent)]
    assert len(progress) == 2
    assert progress[0].text == "> Reading context"
    assert progress[1].text == "✓ Tool use complete"

    # Progress must not bleed into chunk or warning channels
    chunks = [e for e in events if isinstance(e, ReviewChunkEvent)]
    assert all(PROGRESS_MARKER not in e.text for e in chunks)
    warns = [e for e in events if isinstance(e, ReviewWarningEvent)]
    assert not warns


async def test_analyze_comments_returns_empty_for_no_comments():
    adapter = _FakeAdapter()
    assert await adapter.analyze_comments("acme/repo", 1, []) == []


async def test_analyze_comments_skips_stderr_chunks_when_parsing(monkeypatch):
    adapter = _FakeAdapter()
    payload = '[{"author":"a","criticality":"P3","valid":true,"interest":"low","summary":"s","original_body":"o"}]'
    monkeypatch.setattr(
        adapter,
        "_invoke_stream",
        _async_iter([f"{STDERR_MARKER}noise", payload]),
    )

    out = await adapter.analyze_comments(
        "acme/repo", 1, [Comment(id=1, author="a", body="hi")]
    )
    assert out and out[0]["author"] == "a"


async def test_stream_fix_skips_stderr_chunks(monkeypatch):
    adapter = _FakeAdapter()
    monkeypatch.setattr(
        adapter,
        "_invoke_stream",
        _async_iter([f"{STDERR_MARKER}noise", "edit foo.py\n", "done\n"]),
    )
    chunks = []
    async for ev in adapter.stream_fix("/tmp/wt", "acme/repo", 1, "fix"):
        chunks.append(ev.text)
    assert chunks == ["edit foo.py\n", "done\n"]


async def test_generate_text_strips_stderr_and_backticks(monkeypatch):
    adapter = _FakeAdapter()
    monkeypatch.setattr(
        adapter,
        "_invoke_stream",
        _async_iter([f"{STDERR_MARKER}warn", "`fix: bump`"]),
    )
    assert await adapter.generate_text("hi") == "fix: bump"


async def test_generate_text_raises_on_oversized(monkeypatch):
    adapter = _FakeAdapter()
    monkeypatch.setattr(
        adapter,
        "_invoke_stream",
        _async_iter(["x" * 800]),
    )
    with pytest.raises(ProviderError):
        await adapter.generate_text("hi")


# ---- shared parsing helpers ------------------------------------------------


class TestParseReviewOutput:
    def test_parses_valid_json(self):
        review = parse_review_output(
            '{"summary": "ok", "findings": [{"criticality": "P0", "title": "t",'
            ' "description": "d", "file": "a.py", "line": "10"}]}',
            provider="fake",
        )
        assert review.summary == "ok"
        assert review.findings[0].priority == "P0"
        assert review.findings[0].line == 10

    def test_falls_back_on_garbage(self):
        review = parse_review_output("nope", provider="fake")
        assert review.findings == []
        assert review.raw_output == "nope"

    def test_extracts_json_with_noise(self):
        review = parse_review_output(
            'preface\n{"summary":"s","findings":[]}\ntail',
            provider="fake",
        )
        assert review.summary == "s"

    def test_parses_confidence(self):
        review = parse_review_output(
            '{"summary": "ok", "findings": ['
            '{"criticality": "P1", "title": "a", "description": "d", "confidence": "HIGH"},'
            '{"criticality": "P2", "title": "b", "description": "d", "confidence": "maybe"},'
            '{"criticality": "P3", "title": "c", "description": "d"}]}',
            provider="fake",
        )
        assert review.findings[0].confidence == "high"
        assert review.findings[1].confidence is None  # unknown value normalized away
        assert review.findings[2].confidence is None


class TestParseAnalyzeOutput:
    def test_parses_array(self):
        out = parse_analyze_output('preface[{"x": 1}, {"x": 2}]tail')
        assert out == [{"x": 1}, {"x": 2}]

    def test_returns_empty_on_garbage(self):
        assert parse_analyze_output("nope") == []
