"""ReviewService — orchestrates AI, VCS, and cache ports for PR review.

When the PR diff exceeds REVIEW_DIFF_MAX_CHARS, the review is fanned out
across multiple parallel sub-calls (one per packed chunk) and the per-chunk
Reviews are merged into a single Review with a structured summary.
"""
import asyncio
import logging
import os
import time
from collections import Counter
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from app.domain.models import Finding, Review
from app.ports.ai_provider import (
    AIProvider,
    ReviewChunkEvent,
    ReviewResultEvent,
    ReviewStreamEvent,
    ReviewWarningEvent,
)
from app.ports.cache_port import CachePort
from app.ports.vcs_port import VCSPort
from app.services import metrics
from app.services._diff_splitter import Chunk, pack_chunks, split_unified_diff

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CHARS = 30000
_DEFAULT_MAX_CONCURRENCY = 3


class ReviewService:
    def __init__(self, ai: AIProvider, cache: CachePort, vcs: VCSPort) -> None:
        self._ai = ai
        self._cache = cache
        self._vcs = vcs

    async def get_or_run_review(self, repo_full_name: str, pr_number: int) -> Review:
        """Return cached review or run a fresh one."""
        cached = self._cache.get_review(repo_full_name, pr_number)
        if cached is not None:
            return cached
        return await self._run_review(repo_full_name, pr_number)

    async def rerun_review(self, repo_full_name: str, pr_number: int) -> Review:
        """Always run a fresh review, overwriting any cache."""
        return await self._run_review(repo_full_name, pr_number)

    async def stream_review(
        self, repo_full_name: str, pr_number: int, model: str | None = None
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        """Stream review events. Splits & merges large diffs transparently."""
        t0 = time.monotonic()
        diff = self._vcs.get_diff(repo_full_name, pr_number)
        head_sha = self._fetch_head_sha(repo_full_name, pr_number)
        max_chars = _read_int_env("REVIEW_DIFF_MAX_CHARS", _DEFAULT_MAX_CHARS)
        max_concurrency = _read_int_env("REVIEW_MAX_CONCURRENCY", _DEFAULT_MAX_CONCURRENCY)

        if len(diff) <= max_chars:
            async for event in self._ai.stream_review(
                repo_full_name, pr_number, diff, model=model
            ):
                if isinstance(event, ReviewResultEvent):
                    self._finalize_review(
                        event.review, repo_full_name, pr_number, head_sha,
                        t0=t0, model=model, diff_chars=len(diff),
                        chunks=1, files=1, failed_chunks=0,
                    )
                yield event
            return

        async for event in self._stream_split_review(
            repo_full_name, pr_number, diff, model, max_chars, max_concurrency,
            head_sha=head_sha, t0=t0,
        ):
            yield event

    def is_review_stale(self, repo_full_name: str, pr_number: int, review: Review) -> bool:
        """True when the review was run against an older head commit.

        Unknown (no recorded SHA, or VCS lookup failure) → False: a staleness
        check must never block returning the cached review.
        """
        if not review.head_sha:
            return False
        current = self._fetch_head_sha(repo_full_name, pr_number)
        return current is not None and current != review.head_sha

    def _fetch_head_sha(self, repo_full_name: str, pr_number: int) -> str | None:
        """Best-effort head SHA lookup — never raises."""
        try:
            sha = self._vcs.get_pr_head_sha(repo_full_name, pr_number)
        except Exception as exc:  # noqa: BLE001 — staleness is advisory only
            logger.warning(
                "review-service | head sha lookup failed | repo=%s pr=#%d error=%s",
                repo_full_name, pr_number, exc,
            )
            return None
        return sha if isinstance(sha, str) and sha else None

    def _finalize_review(
        self,
        review: Review,
        repo_full_name: str,
        pr_number: int,
        head_sha: str | None,
        *,
        t0: float,
        model: str | None,
        diff_chars: int,
        chunks: int,
        files: int,
        failed_chunks: int,
    ) -> None:
        """Stamp provenance on the review, persist it, and record run metrics."""
        review.head_sha = head_sha
        review.created_at = datetime.now(timezone.utc).isoformat()
        self._cache.save_review(repo_full_name, pr_number, review)
        counts = Counter(f.priority for f in review.findings)
        metrics.record(
            "review",
            repo=repo_full_name,
            pr=pr_number,
            provider=getattr(self._ai, "active", None),
            model=model,
            duration_ms=int((time.monotonic() - t0) * 1000),
            diff_chars=diff_chars,
            chunks=chunks,
            files=files,
            failed_chunks=failed_chunks,
            findings_total=len(review.findings),
            findings_p0=counts.get("P0", 0),
            findings_p1=counts.get("P1", 0),
            findings_p2=counts.get("P2", 0),
            findings_p3=counts.get("P3", 0),
        )

    async def _stream_split_review(
        self,
        repo_full_name: str,
        pr_number: int,
        diff: str,
        model: str | None,
        max_chars: int,
        max_concurrency: int,
        *,
        head_sha: str | None = None,
        t0: float | None = None,
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        if t0 is None:
            t0 = time.monotonic()
        files = split_unified_diff(diff)
        chunks = pack_chunks(files, max_chars)
        truncated = [path for c in chunks for path in c.truncated_files]

        warning_lines = [
            f"Diff too large ({len(diff)} chars), split into {len(chunks)} chunks "
            f"across {len(files)} files."
        ]
        warning_lines += [f"Truncated file: {p}" for p in truncated]
        yield ReviewWarningEvent(warning_lines)

        logger.info(
            "review-service | split | repo=%s pr=#%d diff=%d chars chunks=%d files=%d truncated=%d concurrency=%d",
            repo_full_name, pr_number, len(diff), len(chunks), len(files),
            len(truncated), max_concurrency,
        )

        # Run all chunks under a semaphore; stream their ChunkEvents/Warnings
        # through a queue while collecting their final Review (or exception).
        queue: asyncio.Queue = asyncio.Queue()
        sentinel = object()
        sem = asyncio.Semaphore(max_concurrency)

        async def run_one(chunk: Chunk) -> tuple[Chunk, Review | None, BaseException | None]:
            review: Review | None = None
            error: BaseException | None = None
            try:
                async with sem:
                    try:
                        async for ev in self._ai.stream_review(
                            repo_full_name, pr_number, chunk.content, model=model
                        ):
                            if isinstance(ev, (ReviewChunkEvent, ReviewWarningEvent)):
                                await queue.put(ev)
                            elif isinstance(ev, ReviewResultEvent):
                                review = ev.review
                    except Exception as exc:  # noqa: BLE001 — propagate as warning
                        error = exc
            finally:
                # ALWAYS push the sentinel so the consumer never deadlocks,
                # even if this task is cancelled mid-stream.
                await queue.put((sentinel, chunk, review, error))
            return chunk, review, error

        tasks = [asyncio.create_task(run_one(c)) for c in chunks]
        results: list[tuple[Chunk, Review | None, BaseException | None]] = []

        try:
            done_count = 0
            while done_count < len(chunks):
                item = await queue.get()
                if isinstance(item, tuple) and item and item[0] is sentinel:
                    _, chunk, review, error = item
                    results.append((chunk, review, error))
                    done_count += 1
                else:
                    yield item
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        # Emit per-failure warnings.
        failed_count = 0
        sub_summaries: list[str] = []
        findings: list[Finding] = []
        for chunk, review, error in results:
            if error is not None or review is None:
                failed_count += 1
                if error is not None:
                    msg = (
                        f"Chunk failed: files={chunk.files} "
                        f"error={type(error).__name__}: {error}"
                    )
                else:
                    msg = f"Chunk failed: files={chunk.files} no result returned"
                yield ReviewWarningEvent([msg])
                logger.warning("review-service | chunk failed | %s", msg)
                continue
            findings.extend(review.findings)
            if review.summary:
                sub_summaries.append(review.summary)

        merged_summary = (
            f"Reviewed across {len(chunks)} chunks "
            f"({len(files)} files, {failed_count} failed)."
        )
        if sub_summaries:
            merged_summary += "\n\n" + "\n\n".join(f"- {s}" for s in sub_summaries)

        # Sort findings by priority so P0 appears first, then P1, P2, P3.
        findings.sort(key=lambda f: f.priority)

        merged = Review(summary=merged_summary, findings=findings)
        logger.info(
            "review-service | split done | repo=%s pr=#%d chunks=%d succeeded=%d failed=%d findings=%d",
            repo_full_name, pr_number, len(chunks),
            len(chunks) - failed_count, failed_count, len(findings),
        )
        self._finalize_review(
            merged, repo_full_name, pr_number, head_sha,
            t0=t0, model=model, diff_chars=len(diff),
            chunks=len(chunks), files=len(files), failed_chunks=failed_count,
        )
        yield ReviewResultEvent(review=merged)

    async def _run_review(self, repo_full_name: str, pr_number: int) -> Review:
        review: Review | None = None
        async for event in self.stream_review(repo_full_name, pr_number):
            if isinstance(event, ReviewResultEvent):
                review = event.review
        if review is None:
            review = Review(summary="No review result received.", findings=[])
        return review

    def clear_cache(self, repo_full_name: str, pr_number: int) -> None:
        self._cache.clear_review(repo_full_name, pr_number)


def _read_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        logger.warning("review-service | invalid %s=%r, using default %d", name, raw, default)
        return default
