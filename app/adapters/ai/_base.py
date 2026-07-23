"""Shared infrastructure for CLI-based AI provider adapters.

Why this exists: ``opencode`` and ``claude-code`` are two different binaries
with two different argv conventions, but everything that *surrounds* the
subprocess call is identical — the prompt template, the JSON contract, the
stdout-line streaming, the stderr triage, the timeout handling, the exit-code
handling, and the four AIProvider methods (review / analyze / fix / generate).

Without a base class each adapter ended up with ~250 lines of nearly identical
code, which meant any fix landed in only one of them. The base class below
captures everything that's shared and exposes a tiny set of hook methods for
the per-CLI quirks.

Adapters supply:

- :meth:`BaseCLIAIAdapter.build_invocation` — argv + prompt-passing strategy.
- :meth:`BaseCLIAIAdapter.normalize_model` — optional model-name translation
  (claude-code strips opencode's ``provider/model`` prefix, for instance).
- :meth:`BaseCLIAIAdapter.classify_stderr` — categorise a stderr line as
  ``info`` / ``warning`` / ``skip`` so progress logs don't flood the warning
  panel.
- :meth:`BaseCLIAIAdapter.extra_failure_warnings` — surface CLI-specific
  hints when the run fails (claude-code uses this to forward Bedrock model
  suggestions to the user).
- :meth:`BaseCLIAIAdapter.before_run` — pre-run side-effects (opencode writes
  permission config in the worktree before fix runs).

Everything else — the streaming loop, the parsing, the AIProvider plumbing —
lives here.
"""
from __future__ import annotations

import abc
import asyncio
import logging
import os
import re
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from app.adapters._subprocess import clean_env
from app.adapters.ai._parsing import parse_analyze_output, parse_review_output
from app.adapters.ai._prompts import (
    ANALYZE_PROMPT,
    FIX_PROMPT,
    REVIEW_PROMPT,
    SYNTHESIZE_PROMPT,
)
from app.domain.exceptions import ProviderError
from app.domain.models import Comment
from app.ports.ai_provider import (
    AIProvider,
    FixChunkEvent,
    ReviewChunkEvent,
    ReviewProgressEvent,
    ReviewResultEvent,
    ReviewStreamEvent,
    ReviewWarningEvent,
)

# Set LOG_AI_CHUNKS=1 to log the instruction prompt and each stdout chunk
# (first 300 chars) at INFO level.  Useful for local debugging; avoid in
# production when LOG_OPS=1 since content will land in ops.jsonl.
_LOG_AI_CHUNKS = os.getenv("LOG_AI_CHUNKS", "").strip().lower() in {"1", "true", "yes"}

# Strip ANSI/VT100 escape sequences from a string.  OpenCode's TUI emits
# cursor-movement and line-erase codes (e.g. \033[K, \r) even when its stderr
# is a pipe.  Left in place they make log lines look empty in terminals and
# prevent the info-prefix classifier from matching otherwise-valid lines.
_ANSI_RE = re.compile(
    r"\x1b"           # ESC
    r"(?:"
    r"\[[0-9;]*[A-Za-z]"    # CSI sequences: ESC [ … <letter>
    r"|\][^\x07]*\x07"      # OSC sequences: ESC ] … BEL
    r"|[@-_]"               # two-byte sequences: ESC <0x40-0x5F>
    r")"
    r"|\r"             # bare carriage-return (cursor-to-column-0)
)


def _strip_ansi(text: str) -> str:
    """Remove ANSI/VT100 escape sequences and bare carriage returns."""
    return _ANSI_RE.sub("", text)


# Sentinel used inside the streamed chunks to mark a stderr line so the
# adapter's `stream_review` can split them out and emit ReviewWarningEvent.
# The chunk shape is `\x00STDERR\x00<line>` and the marker length is 8 bytes
# (NULL + "STDERR" + NULL).
STDERR_MARKER = "\x00STDERR\x00"
STDERR_MARKER_LEN = len(STDERR_MARKER)

# Sentinel for real-time stderr *progress* lines (info-classified) streamed
# during the subprocess run so the frontend can show live activity before the
# first stdout chunk arrives.  Shape: `\x00PROGRESS\x00<line>`.
PROGRESS_MARKER = "\x00PROGRESS\x00"
PROGRESS_MARKER_LEN = len(PROGRESS_MARKER)

# Internal tags used by the fan-in mux queue inside stream_cli.
_MUX_STDOUT = "stdout"
_MUX_PROGRESS = "progress"
_MUX_STDOUT_DONE = "stdout_done"
_MUX_STDERR_DONE = "stderr_done"
_MUX_TIMEOUT = "timeout"

Mode = Literal["review", "analyze", "generate", "fix"]
StderrCategory = Literal["info", "warning", "skip"]

DEFAULT_REVIEW_TIMEOUT = 300
DEFAULT_FIX_TIMEOUT = 600
DEFAULT_GENERATE_TIMEOUT = 60
_GENERATE_TEXT_MAX_CHARS = 500
_FAILURE_STDOUT_SNIPPET_CHARS = 4000
_FAILURE_STDOUT_LINE_CAP = 40
# Hard cap on cleanup (kill + reap + stderr drain) so a misbehaving CLI that
# ignores SIGKILL doesn't leave the request hanging forever.
_CLEANUP_TIMEOUT = 5.0


@dataclass
class CLIInvocation:
    """How a single CLI run should be launched.

    ``argv`` is the full argv list. The prompt may be passed either as a
    positional argument inside ``argv`` (the default) or via stdin when
    ``stdin_payload`` is set — opencode falls back to stdin for prompts
    larger than ~4 KB to dodge ARG_MAX issues on some platforms.
    """

    argv: list[str]
    cwd: str | None = None
    stdin_payload: str | None = None
    extra_env: dict[str, str] = field(default_factory=dict)


class BaseCLIAIAdapter(AIProvider, abc.ABC):
    """Template-method base class for AI adapters that shell out to a CLI."""

    #: Stable name used in logs, error messages, and the parser logger key.
    cli_name: ClassVar[str]
    #: Executable name (the thing that must be on ``PATH``).
    cli_executable: ClassVar[str]
    #: Whether a non-zero exit with *some* stdout must raise
    #: :class:`ProviderError`. Claude Code is strict — silently parsing a
    #: 422-response stderr would surface a hollow "0 findings" review.
    #: Opencode is lenient because some non-zero exits still ship usable
    #: output. Either way, a non-zero exit with *zero* stdout always raises
    #: (see ``stream_cli``) — there's nothing to salvage from a hard crash.
    raise_on_nonzero_exit: ClassVar[bool] = False

    review_prompt: ClassVar[str] = REVIEW_PROMPT
    fix_prompt: ClassVar[str] = FIX_PROMPT
    analyze_prompt: ClassVar[str] = ANALYZE_PROMPT
    synthesize_prompt: ClassVar[str] = SYNTHESIZE_PROMPT

    def _effective_prompt(self, name: str, default: str) -> str:
        """Return the user's custom prompt for ``name`` or fall back to ``default``.

        Resolved lazily through :mod:`app.services.settings_store` so a custom
        prompt edited from the Settings UI takes effect without a restart.
        Never raises — a broken override must not break a review run.
        """
        try:
            from app.services import settings_store
            return settings_store.get_prompt(name)
        except Exception:  # noqa: BLE001 — prompt overrides are best-effort
            return default

    # ---- hook methods ------------------------------------------------------

    @abc.abstractmethod
    def build_invocation(
        self,
        prompt: str,
        *,
        mode: Mode,
        cwd: str | None,
        model: str | None,
    ) -> CLIInvocation:
        """Return the argv + I/O strategy for this run."""

    def normalize_model(self, model: str | None) -> str | None:
        """Hook for adapter-specific model-name translation.

        The default is identity so opencode (which accepts any string the
        upstream registry supports) doesn't have to override it. Claude Code
        uses this to strip the optional ``anthropic/`` provider prefix.
        """
        return model

    def classify_stderr(self, line: str) -> StderrCategory:
        """Decide how to surface a single decoded stderr line.

        ``skip`` swallows the line entirely (used to silence known noise
        such as opencode's ``[STALE]`` warnings). ``info`` logs at INFO level
        so progress markers don't appear in the warning panel. ``warning``
        is the default and forwards the line to the caller.
        """
        return "warning"

    def extra_failure_warnings(
        self,
        *,
        rc: int,
        stdout_lines: list[str],
        stderr_lines: list[str],
    ) -> list[str]:
        """Adapter-specific extra warnings to append when ``rc != 0``.

        Claude Code uses this to forward the ``Try --model to switch to X``
        Bedrock hint as a structured warning the UI can promote to a tip.
        """
        return []

    def before_run(self, *, cwd: str | None, mode: Mode) -> None:
        """Hook for pre-run side effects (e.g. writing config files)."""

    # ---- shared streaming loop --------------------------------------------

    async def stream_cli(
        self,
        message: str,
        *,
        mode: Mode,
        context: str | None = None,
        cwd: str | None = None,
        model: str | None = None,
        timeout: int = DEFAULT_REVIEW_TIMEOUT,
    ) -> AsyncGenerator[str, None]:
        """Run the CLI and stream stdout lines plus real-time progress markers.

        Yields three kinds of chunks:
        - plain text lines (raw stdout)
        - ``PROGRESS_MARKER + line`` for info-classified stderr lines, emitted
          in real time so the frontend can show live activity before the first
          stdout chunk arrives
        - ``STDERR_MARKER + line`` for warning-classified stderr lines, emitted
          after the process exits (same as before)

        Both stdout and stderr are read concurrently via an asyncio.Queue fan-in
        so progress messages are not delayed by waiting for the next stdout line.
        """
        prompt = message if context is None else f"{message}\n\n---\n\n{context}"
        normalized_model = self.normalize_model(model)
        prompt_kb = len(prompt.encode()) / 1024
        logger = logging.getLogger(self.cli_name)
        logger.info(
            "%s | starting | mode=%s cwd=%s prompt=%.1f KB model=%s",
            self.cli_name, mode, cwd or ".", prompt_kb, normalized_model or "default",
        )
        if _LOG_AI_CHUNKS:
            # Log the instruction (message) but NOT the context/diff so that
            # source-code content never ends up in this log line.
            msg_preview = message.replace("\n", "↵")
            if len(msg_preview) > 300:
                msg_preview = msg_preview[:300] + "…"
            logger.info("%s | prompt | %s", self.cli_name, msg_preview)
        self.before_run(cwd=cwd, mode=mode)

        invocation = self.build_invocation(
            prompt, mode=mode, cwd=cwd, model=normalized_model
        )
        env = clean_env()
        if invocation.extra_env:
            env.update(invocation.extra_env)

        t_start = time.monotonic()
        proc: asyncio.subprocess.Process | None = None
        stdout_feeder: asyncio.Task[None] | None = None
        stderr_feeder: asyncio.Task[None] | None = None
        stdin_task: asyncio.Task[None] | None = None
        output_lines = 0
        stdout_capture: list[str] = []
        timed_out = False
        stderr_warning_lines: list[str] = []
        try:
            if invocation.stdin_payload is not None:
                proc = await asyncio.create_subprocess_exec(
                    *invocation.argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=invocation.cwd,
                    env=env,
                )
                if proc.stdin is None:
                    raise ProviderError(
                        f"{self.cli_name} subprocess did not open stdin"
                    )
                # Run the stdin writer concurrently so a >64 KB prompt doesn't
                # block on the kernel pipe buffer while we're still trying to
                # drain stdout. _drain_stdin handles the write+drain+close
                # sequence with broken-pipe tolerance.
                stdin_task = asyncio.create_task(
                    _drain_stdin(proc.stdin, invocation.stdin_payload, logger, self.cli_name)
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *invocation.argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=invocation.cwd,
                    env=env,
                )

            if proc.stdout is None or proc.stderr is None:
                raise ProviderError(
                    f"{self.cli_name} subprocess did not open stdout/stderr"
                )

            # Fan-in queue: both stdout and stderr feeders write tagged tuples;
            # the main loop below reads them in arrival order so progress lines
            # reach the caller before the first stdout line appears.
            mux: asyncio.Queue[tuple] = asyncio.Queue()
            stdout_feeder = asyncio.create_task(
                _feed_stdout_to_queue(proc.stdout, mux, timeout)
            )
            stderr_feeder = asyncio.create_task(
                _feed_stderr_to_queue(proc.stderr, self.classify_stderr, mux, logger, self.cli_name)
            )

            stdout_done = False
            stderr_done = False
            total_stdout_bytes = 0
            _logged_kb = 0   # last KB boundary we logged a progress line for

            while not (stdout_done and stderr_done):
                item = await mux.get()
                tag = item[0]

                if tag == _MUX_STDOUT:
                    decoded = item[1].decode()
                    output_lines += 1
                    stdout_capture.append(decoded)
                    total_stdout_bytes += len(decoded)
                    # Log arrival of the first chunk (shows how long startup took).
                    if output_lines == 1:
                        logger.info(
                            "%s | first chunk | elapsed=%.1fs",
                            self.cli_name, time.monotonic() - t_start,
                        )
                    # Log a brief progress line each time we cross a new KB boundary
                    # so the backend operator can see that data is flowing.
                    current_kb = total_stdout_bytes // 1024
                    if current_kb > _logged_kb:
                        _logged_kb = current_kb
                        logger.info(
                            "%s | streaming | lines=%d kb=%d elapsed=%.1fs",
                            self.cli_name, output_lines, current_kb,
                            time.monotonic() - t_start,
                        )
                    if _LOG_AI_CHUNKS:
                        preview = decoded.rstrip()
                        if len(preview) > 300:
                            preview = preview[:300] + "…"
                        logger.info("%s | chunk #%d | %s", self.cli_name, output_lines, preview)
                    else:
                        logger.debug("%s | stdout | %s", self.cli_name, decoded.rstrip())
                    yield decoded
                elif tag == _MUX_PROGRESS:
                    yield f"{PROGRESS_MARKER}{item[1]}"
                elif tag == _MUX_STDOUT_DONE:
                    stdout_done = True
                elif tag == _MUX_STDERR_DONE:
                    stderr_done = True
                    stderr_warning_lines = item[1]
                elif tag == _MUX_TIMEOUT:
                    proc.kill()
                    elapsed = time.monotonic() - t_start
                    logger.error(
                        "%s | TIMEOUT after %.1fs | killing process",
                        self.cli_name, elapsed,
                    )
                    yield "\n[TIMEOUT]\n"
                    timed_out = True
                    # Exit the fan-in loop immediately; feeder cleanup happens
                    # in the finally block below.
                    stdout_done = True
                    stderr_done = True

            # Drain any items that arrived after we broke out (e.g., stderr
            # completing right after a timeout).
            while not mux.empty():
                try:
                    item = mux.get_nowait()
                    if item[0] == _MUX_STDERR_DONE:
                        stderr_warning_lines = item[1]
                except asyncio.QueueEmpty:
                    break

            # If stderr feeder is still alive (e.g. after timeout), wait briefly
            # to collect any final warning lines before reaping the process.
            if stderr_feeder is not None and not stderr_feeder.done():
                try:
                    await asyncio.wait_for(stderr_feeder, timeout=_CLEANUP_TIMEOUT)
                    while not mux.empty():
                        try:
                            item = mux.get_nowait()
                            if item[0] == _MUX_STDERR_DONE:
                                stderr_warning_lines = item[1]
                        except asyncio.QueueEmpty:
                            break
                except asyncio.TimeoutError:
                    stderr_feeder.cancel()
                    logger.warning(
                        "%s | stderr drain timed out after %.1fs",
                        self.cli_name, _CLEANUP_TIMEOUT,
                    )

            # Bound the post-stream reap so a CLI that ignores SIGKILL can't
            # deadlock the request handler.
            try:
                await asyncio.wait_for(proc.wait(), timeout=_CLEANUP_TIMEOUT)
            except asyncio.TimeoutError:
                logger.error(
                    "%s | post-stream wait() timed out after %.1fs",
                    self.cli_name, _CLEANUP_TIMEOUT,
                )
        except asyncio.CancelledError:
            # Consumer cancelled us (e.g. SSE client disconnected). Reap the
            # subprocess and helper tasks before propagating so we don't leak
            # processes / file descriptors.
            await _cleanup(proc, [stdout_feeder, stderr_feeder, stdin_task], logger, self.cli_name)
            raise
        except Exception:  # noqa: BLE001 — same cleanup, then re-raise.
            await _cleanup(proc, [stdout_feeder, stderr_feeder, stdin_task], logger, self.cli_name)
            raise
        finally:
            # Guarantee all helper tasks are cancelled and reaped even on the
            # normal exit path (they should already be done, but be defensive).
            for _task in (stdin_task, stdout_feeder, stderr_feeder):
                if _task is not None and not _task.done():
                    _task.cancel()
                    try:
                        await asyncio.wait_for(_task, timeout=_CLEANUP_TIMEOUT)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                    except Exception:  # noqa: BLE001
                        pass

        elapsed = time.monotonic() - t_start
        rc = proc.returncode if proc is not None else None
        if rc == 0:
            logger.info(
                "%s | done | exit=%s lines=%d elapsed=%.1fs",
                self.cli_name, rc, output_lines, elapsed,
            )
        else:
            logger.warning(
                "%s | done | exit=%s lines=%d elapsed=%.1fs",
                self.cli_name, rc, output_lines, elapsed,
            )

        warning_lines = list(stderr_warning_lines)
        captured_stdout = "".join(stdout_capture).strip()
        is_failure = rc is None or rc != 0
        if is_failure:
            if output_lines == 0:
                warning_lines.insert(
                    0,
                    f"[{self.cli_name} exited with code {rc} and produced no output]",
                )
            else:
                warning_lines.insert(0, f"[{self.cli_name} exited with code {rc}]")
                if captured_stdout:
                    warning_lines.append(f"[{self.cli_name} stdout]")
                    snippet = captured_stdout[:_FAILURE_STDOUT_SNIPPET_CHARS]
                    for cap_line in snippet.splitlines()[:_FAILURE_STDOUT_LINE_CAP]:
                        warning_lines.append(cap_line)

            warning_lines.extend(
                self.extra_failure_warnings(
                    rc=rc if rc is not None else -1,
                    stdout_lines=stdout_capture,
                    stderr_lines=stderr_warning_lines,
                )
            )

        for err_line in warning_lines:
            yield f"{STDERR_MARKER}{err_line}"

        if timed_out and self.raise_on_nonzero_exit:
            raise ProviderError(
                f"{self.cli_executable} timed out after {timeout}s"
            )

        # Leniency (raise_on_nonzero_exit=False) exists for CLIs that still
        # ship usable stdout on a non-zero exit. It must not apply when there
        # is zero output to salvage — that's a hard failure (e.g. the CLI
        # crashed on startup), and silently downgrading it to a "0 findings"
        # review hides the real error from the caller.
        must_raise = self.raise_on_nonzero_exit or output_lines == 0
        if is_failure and must_raise and not timed_out:
            first_stdout_line = next(
                (line for line in captured_stdout.splitlines() if line.strip()), ""
            )
            first_stderr_line = next(
                (line for line in stderr_warning_lines if line.strip()), ""
            )
            detail = first_stdout_line or first_stderr_line or "(no output)"
            if len(detail) > 200:
                detail = detail[:197] + "..."
            logger.error(
                "%s | failed | exit=%s | first_line=%r",
                self.cli_name, rc, detail,
            )
            raise ProviderError(
                f"{self.cli_executable} exited with code {rc}: {detail}"
            )

    # ---- AIProvider implementation ----------------------------------------

    async def stream_review(
        self,
        repo_full_name: str,
        pr_number: int,
        diff: str,
        model: str | None = None,
        timeout: int = DEFAULT_REVIEW_TIMEOUT,
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        message = self._effective_prompt("review", self.review_prompt).format(
            pr_number=pr_number, repo_full_name=repo_full_name
        )
        async for event in self._stream_structured_review(
            message, diff, repo_full_name=repo_full_name, pr_number=pr_number,
            model=model, timeout=timeout, label="review",
        ):
            yield event

    async def stream_synthesis(
        self,
        repo_full_name: str,
        pr_number: int,
        synthesis_input: str,
        model: str | None = None,
        timeout: int = DEFAULT_REVIEW_TIMEOUT,
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        message = self._effective_prompt("synthesize", self.synthesize_prompt).format(
            pr_number=pr_number, repo_full_name=repo_full_name
        )
        async for event in self._stream_structured_review(
            message, synthesis_input, repo_full_name=repo_full_name,
            pr_number=pr_number, model=model, timeout=timeout, label="synthesis",
        ):
            yield event

    async def _stream_structured_review(
        self,
        message: str,
        context: str,
        *,
        repo_full_name: str,
        pr_number: int,
        model: str | None,
        timeout: int,
        label: str,
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        """Shared streaming loop for calls that return the review JSON schema.

        Both the review pass and the synthesis pass send an instruction plus a
        context payload and expect the same ``{"summary", "findings"}`` object
        back, so they share one loop. ``label`` distinguishes them in logs.
        """
        context_kb = len(context.encode()) / 1024
        logger = logging.getLogger(self.cli_name)
        logger.info(
            "%s | start | provider=%s repo=%s pr=#%d context=%.1f KB model=%s timeout=%ds",
            label, self.cli_name, repo_full_name, pr_number, context_kb,
            model or "default", timeout,
        )
        chunks: list[str] = []
        warning_lines: list[str] = []
        async for chunk in self._invoke_stream(
            message, mode="review", context=context, model=model, timeout=timeout,
        ):
            if chunk.startswith(PROGRESS_MARKER):
                yield ReviewProgressEvent(text=chunk[PROGRESS_MARKER_LEN:])
            elif chunk.startswith(STDERR_MARKER):
                warning_lines.append(chunk[STDERR_MARKER_LEN:])
            else:
                chunks.append(chunk)
                yield ReviewChunkEvent(text=chunk)

        if warning_lines:
            yield ReviewWarningEvent(lines=warning_lines)

        full_output = "".join(chunks).strip()
        review = parse_review_output(full_output, provider=self.cli_name)
        logger.info(
            "%s | complete | provider=%s repo=%s pr=#%d findings=%d",
            label, self.cli_name, repo_full_name, pr_number, len(review.findings),
        )
        yield ReviewResultEvent(review=review)

    async def analyze_comments(
        self,
        repo_full_name: str,
        pr_number: int,
        comments: list[Comment],
    ) -> list[dict]:
        if not comments:
            return []

        comments_text = "\n\n".join(
            f"Comment [id={c.id}] by {c.author}:\n{c.body}" for c in comments
        )
        message = self._effective_prompt("analyze", self.analyze_prompt).format(
            pr_number=pr_number, repo_full_name=repo_full_name
        )

        output_parts: list[str] = []
        async for chunk in self._invoke_stream(
            message, mode="analyze", context=comments_text[:20000],
        ):
            if chunk.startswith(STDERR_MARKER) or chunk.startswith(PROGRESS_MARKER):
                continue
            output_parts.append(chunk)
        return parse_analyze_output("".join(output_parts))

    async def stream_fix(
        self,
        repo_dir: str,
        repo_full_name: str,
        pr_number: int,
        comment_body: str,
    ) -> AsyncGenerator[FixChunkEvent, None]:
        prompt = self._effective_prompt("fix", self.fix_prompt).format(
            pr_number=pr_number,
            repo_full_name=repo_full_name,
            comment_body=comment_body,
        )
        async for chunk in self._invoke_stream(
            prompt, mode="fix", cwd=repo_dir, timeout=DEFAULT_FIX_TIMEOUT,
        ):
            if chunk.startswith(STDERR_MARKER) or chunk.startswith(PROGRESS_MARKER):
                continue
            yield FixChunkEvent(text=chunk)

    async def generate_text(self, prompt: str, timeout: int = DEFAULT_GENERATE_TIMEOUT) -> str:
        parts: list[str] = []
        stderr_parts: list[str] = []
        async for chunk in self._invoke_stream(prompt, mode="generate", timeout=timeout):
            if chunk.startswith(STDERR_MARKER):
                stderr_parts.append(chunk[STDERR_MARKER_LEN:])
                continue
            if chunk.startswith(PROGRESS_MARKER):
                continue
            parts.append(chunk)
        if stderr_parts:
            # generate_text return type is `str` so we can't surface stderr
            # to the caller, but we MUST log it — silently dropping a model
            # warning here is what made debugging the original Claude Code
            # connector painful.
            logging.getLogger(self.cli_name).warning(
                "%s | generate_text | %d stderr line(s): %s",
                self.cli_name, len(stderr_parts), " | ".join(stderr_parts[:5]),
            )
        result = "".join(parts).strip().strip("`").strip()
        if result and len(result) <= _GENERATE_TEXT_MAX_CHARS:
            return result
        raise ProviderError(
            f"generate_text returned unusable output (length={len(result)})"
        )

    # ---- internal seam ----------------------------------------------------

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
        """Indirection layer so subclasses can keep their module-level
        ``_stream_<cli>`` helpers as a stable, monkeypatchable test seam.

        Default implementation delegates straight to :meth:`stream_cli`.
        Subclasses override this only when they want a separate test seam.
        """
        async for chunk in self.stream_cli(
            message,
            mode=mode,
            context=context,
            cwd=cwd,
            model=model,
            timeout=timeout,
        ):
            yield chunk


async def _feed_stdout_to_queue(
    stream: asyncio.StreamReader,
    queue: asyncio.Queue,
    timeout: float,
) -> None:
    """Read stdout line-by-line and push tagged tuples to the fan-in queue.

    Tags: ``(_MUX_STDOUT, bytes)`` for each line, ``(_MUX_TIMEOUT,)`` if the
    per-line timeout fires, ``(_MUX_STDOUT_DONE,)`` on EOF.
    """
    while True:
        try:
            line = await asyncio.wait_for(stream.readline(), timeout=timeout)
        except asyncio.TimeoutError:
            await queue.put((_MUX_TIMEOUT,))
            return
        if not line:
            await queue.put((_MUX_STDOUT_DONE,))
            return
        await queue.put((_MUX_STDOUT, line))


async def _feed_stderr_to_queue(
    stream: asyncio.StreamReader,
    classify: Callable[[str], StderrCategory],
    queue: asyncio.Queue,
    logger: logging.Logger,
    cli_name: str,
) -> None:
    """Read stderr and push to the fan-in queue.

    *Info*-classified lines become ``(_MUX_PROGRESS, text)`` items delivered
    in real time so they can reach the frontend before the first stdout chunk.
    *Warning*-classified lines are accumulated and sent as a single
    ``(_MUX_STDERR_DONE, [lines])`` item on EOF so the existing warning-panel
    behaviour is preserved.  *Skip*-classified lines are silenced entirely.
    """
    warning_lines: list[str] = []
    while True:
        line = await stream.readline()
        if not line:
            await queue.put((_MUX_STDERR_DONE, warning_lines))
            return
        decoded = _strip_ansi(line.decode().rstrip())
        if not decoded:
            continue
        verdict = classify(decoded)
        if verdict == "skip":
            continue
        if verdict == "info":
            logger.info("%s | %s", cli_name, decoded)
            await queue.put((_MUX_PROGRESS, decoded))
        else:
            logger.warning("%s | stderr | %s", cli_name, decoded)
            warning_lines.append(decoded)


async def _drain_stdin(
    stdin: asyncio.StreamWriter,
    payload: str,
    logger: logging.Logger,
    cli_name: str,
) -> None:
    """Write ``payload`` to ``stdin`` then close it, draining the buffer.

    A bare ``write()`` followed by ``close()`` is enough only when the kernel
    pipe buffer (≈64 KB on Linux) holds the whole payload. Above that the
    write blocks until the child reads — and the child can't read until
    we've started forwarding stdout, which we can't do until ``stream_cli``
    enters its read loop. ``await drain()`` releases that deadlock.

    Broken-pipe errors are tolerated: the child may exit before we finish
    writing (e.g. usage error path), and that's not a fatal condition for
    the caller.
    """
    try:
        encoded = payload.encode()
        stdin.write(encoded)
        await stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        logger.info(
            "%s | stdin closed by child before payload fully sent (%d bytes)",
            cli_name, len(payload.encode()),
        )
    except Exception as exc:  # noqa: BLE001 — log unexpected errors and move on
        logger.warning("%s | stdin write failed: %r", cli_name, exc)
    finally:
        try:
            stdin.close()
        except Exception:  # noqa: BLE001
            pass


async def _cleanup(
    proc: asyncio.subprocess.Process | None,
    tasks: list[asyncio.Task | None],
    logger: logging.Logger,
    cli_name: str,
) -> None:
    """Best-effort: kill the subprocess, cancel helper tasks, drain pipes.

    Called when the generator is closed mid-stream (consumer disconnected,
    parent task cancelled, or an exception bubbled up). Always returns —
    never re-raises so it can be safely called from ``except`` / ``finally``.
    """
    if proc is not None and proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass  # already exited between the returncode check and kill()
        except Exception:  # noqa: BLE001 — never let cleanup itself raise
            logger.warning("%s | cleanup kill() failed", cli_name, exc_info=True)
        try:
            await asyncio.wait_for(proc.wait(), timeout=_CLEANUP_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(
                "%s | cleanup wait() timed out; subprocess may be leaked",
                cli_name,
            )
        except Exception:  # noqa: BLE001
            logger.warning("%s | cleanup wait() failed", cli_name, exc_info=True)
    for task in tasks:
        if task is None or task.done():
            continue
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=_CLEANUP_TIMEOUT)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:  # noqa: BLE001 — broken pipe etc., already cancelled.
            pass


def split_stderr_chunk(chunk: str) -> tuple[bool, str]:
    """Helper: ``(is_stderr, payload)`` for a chunk yielded by ``stream_cli``."""
    if chunk.startswith(STDERR_MARKER):
        return True, chunk[STDERR_MARKER_LEN:]
    return False, chunk


__all__ = [
    "BaseCLIAIAdapter",
    "CLIInvocation",
    "DEFAULT_FIX_TIMEOUT",
    "DEFAULT_GENERATE_TIMEOUT",
    "DEFAULT_REVIEW_TIMEOUT",
    "Mode",
    "PROGRESS_MARKER",
    "PROGRESS_MARKER_LEN",
    "STDERR_MARKER",
    "STDERR_MARKER_LEN",
    "StderrCategory",
    "split_stderr_chunk",
]
