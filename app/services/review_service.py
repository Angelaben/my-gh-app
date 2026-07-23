"""ReviewService — orchestrates AI, VCS, and cache ports for PR review.

The whole diff is sent to the provider in a single call whenever it fits
REVIEW_DIFF_MAX_CHARS (the default is sized for large-context models, so
splitting is the exception, not the rule). Only above that budget is the
review fanned out across parallel sub-calls (one per packed chunk); each
sub-call is prepended with a shared PR manifest — the full list of changed
files with +/- counts — so independent chunk reviews keep cross-file
context. The per-chunk Reviews are merged mechanically, then (when
REVIEW_SYNTHESIS is enabled) handed back to the provider for a synthesis
pass that dedupes semantically and writes one coherent summary; any
synthesis failure falls back to the mechanical merge.
"""
import asyncio
import json
import logging
import time
from collections import Counter
from collections.abc import AsyncGenerator
from dataclasses import asdict
from datetime import datetime, timezone

from app.domain.models import Finding, Review
from app.ports.ai_provider import (
    AIProvider,
    ReviewChunkEvent,
    ReviewProgressEvent,
    ReviewResultEvent,
    ReviewStreamEvent,
    ReviewWarningEvent,
)
from app.ports.cache_port import CachePort
from app.ports.vcs_port import VCSPort
from app.services import metrics, settings_store
from app.services._diff_splitter import (
    Chunk,
    FileDiff,
    ManifestEntry,
    build_manifest,
    filter_ignored_files,
    pack_chunks,
    render_chunk_preamble,
    split_unified_diff,
)

logger = logging.getLogger(__name__)

# Ceiling on the synthesis-pass input (findings JSON + summaries + manifest).
# Above this the mechanical merge is kept — re-splitting the synthesis would
# defeat its purpose.
_SYNTHESIS_INPUT_MAX_CHARS = 100_000


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

    def get_cached_review(self, repo_full_name: str, pr_number: int) -> Review | None:
        """Return the persisted review for a PR, or None. Never runs a review."""
        return self._cache.get_review(repo_full_name, pr_number)

    def list_cached_reviews(self, repo_full_name: str) -> dict[int, Review]:
        """Return all persisted reviews for a repo, keyed by PR number.

        Read-only: never triggers a review. Skips any PR whose cache file
        vanished between listing and reading.
        """
        reviews: dict[int, Review] = {}
        for pr_number in self._cache.list_reviewed_prs(repo_full_name):
            review = self._cache.get_review(repo_full_name, pr_number)
            if review is not None:
                reviews[pr_number] = review
        return reviews

    async def stream_review(
        self,
        repo_full_name: str,
        pr_number: int,
        model: str | None = None,
        timeout: int | None = None,
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        """Stream review events. Splits & merges large diffs transparently."""
        t0 = time.monotonic()
        settings = settings_store.get_review_settings()
        if timeout is None:
            timeout = settings["review_timeout"]
        raw_diff = self._vcs.get_diff(repo_full_name, pr_number)
        head_sha = self._fetch_head_sha(repo_full_name, pr_number)
        async for event in self._review_diff_text(
            repo_full_name, pr_number, raw_diff, model, timeout, settings,
            head_sha=head_sha, t0=t0,
        ):
            yield event

    async def _review_diff_text(
        self,
        repo_full_name: str,
        pr_number: int,
        raw_diff: str,
        model: str | None,
        timeout: int,
        settings: dict,
        *,
        head_sha: str | None,
        t0: float,
        finalize: bool = True,
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        """Review an arbitrary diff text, streaming events and a final result.

        Shared by the full-PR review and the incremental review. When
        ``finalize`` is True the merged review is cached and a metrics record is
        written; the incremental flow disables that so it can finalize the
        *merged* (carried + new) review itself.
        """
        max_chars = settings["review_diff_max_chars"]
        max_concurrency = settings["review_max_concurrency"]

        files = split_unified_diff(raw_diff)
        ignored: list[str] = []
        if files:
            files, ignored = filter_ignored_files(files, settings["review_ignore_globs"])
            diff = "".join(f.content for f in files)
        else:
            # No parseable file sections — review the raw diff as-is.
            diff = raw_diff

        if ignored:
            yield ReviewWarningEvent([
                f"Skipped {len(ignored)} generated/vendored file(s) "
                f"(configure via Settings → ignore globs): " + ", ".join(sorted(ignored))
            ])
            logger.info(
                "review-service | ignored files | repo=%s pr=#%d count=%d",
                repo_full_name, pr_number, len(ignored),
            )
            if not files:
                review = Review(
                    summary=(
                        f"All {len(ignored)} changed file(s) matched the review "
                        "ignore filters — nothing to review."
                    ),
                    findings=[],
                )
                if finalize:
                    self._finalize_review(
                        review, repo_full_name, pr_number, head_sha,
                        t0=t0, model=model, diff_chars=0,
                        chunks=0, files=0, failed_chunks=0,
                    )
                yield ReviewResultEvent(review)
                return

        if len(diff) <= max_chars:
            async for event in self._ai.stream_review(
                repo_full_name, pr_number, diff, model=model, timeout=timeout
            ):
                if isinstance(event, ReviewResultEvent) and finalize:
                    self._finalize_review(
                        event.review, repo_full_name, pr_number, head_sha,
                        t0=t0, model=model, diff_chars=len(diff),
                        chunks=1, files=max(len(files), 1), failed_chunks=0,
                    )
                yield event
            return

        async for event in self._stream_split_review(
            repo_full_name, pr_number, diff, files, model, max_chars, max_concurrency,
            head_sha=head_sha, t0=t0, timeout=timeout, finalize=finalize,
            synthesis=bool(settings.get("review_synthesis", True)),
        ):
            yield event

    async def incremental_review(
        self, repo_full_name: str, pr_number: int, model: str | None = None
    ) -> dict:
        """Re-review only the files changed since the cached review's head SHA.

        Findings on files untouched by the new commits are carried over; the
        changed files are freshly reviewed. Returns the merged review plus
        metadata (base/head SHA, changed files, carried/new counts).
        """
        cached = self._cache.get_review(repo_full_name, pr_number)
        if cached is None or not cached.head_sha:
            review = await self._run_review(repo_full_name, pr_number)
            return {
                "review": review, "no_changes": False, "base_sha": None,
                "head_sha": review.head_sha,
                "changed_files": [], "carried": 0, "new": len(review.findings),
            }

        base_sha = cached.head_sha
        head_sha = self._fetch_head_sha(repo_full_name, pr_number)
        if head_sha is None or head_sha == base_sha:
            return {
                "review": cached, "no_changes": True, "base_sha": base_sha,
                "head_sha": head_sha or base_sha,
                "changed_files": [], "carried": len(cached.findings), "new": 0,
            }

        settings = settings_store.get_review_settings()
        t0 = time.monotonic()
        compare_diff = self._vcs.get_diff_between_shas(repo_full_name, base_sha, head_sha)
        changed_files = {f.path for f in split_unified_diff(compare_diff)}

        new_review: Review | None = None
        async for event in self._review_diff_text(
            repo_full_name, pr_number, compare_diff, model,
            settings["review_timeout"], settings,
            head_sha=head_sha, t0=t0, finalize=False,
        ):
            if isinstance(event, ReviewResultEvent):
                new_review = event.review
        if new_review is None:
            new_review = Review(summary="", findings=[])

        carried = [f for f in cached.findings if f.file not in changed_files]
        new_findings = list(new_review.findings)
        merged_findings = _dedupe_findings(carried + new_findings)
        merged_findings.sort(key=lambda f: f.priority)
        merged = Review(
            summary=(
                f"Incremental review: {len(new_findings)} finding(s) from "
                f"{len(changed_files)} changed file(s) since {base_sha[:7]}; "
                f"{len(carried)} finding(s) carried over."
            ),
            findings=merged_findings,
        )
        self._finalize_review(
            merged, repo_full_name, pr_number, head_sha,
            t0=t0, model=model, diff_chars=len(compare_diff),
            chunks=1, files=len(changed_files), failed_chunks=0,
        )
        return {
            "review": merged, "no_changes": False, "base_sha": base_sha,
            "head_sha": head_sha, "changed_files": sorted(changed_files),
            "carried": len(carried), "new": len(new_findings),
        }

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
        files: list[FileDiff],
        model: str | None,
        max_chars: int,
        max_concurrency: int,
        *,
        head_sha: str | None = None,
        t0: float | None = None,
        timeout: int = 300,
        finalize: bool = True,
        synthesis: bool = True,
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        if t0 is None:
            t0 = time.monotonic()
        chunks = pack_chunks(files, max_chars)
        manifest = build_manifest(files)
        truncated = [path for c in chunks for path in c.truncated_files]

        warning_lines = [
            f"Diff too large ({len(diff)} chars), split into {len(chunks)} chunks "
            f"across {len(files)} files. Each chunk carries the full PR file "
            "manifest so sub-reviews keep cross-file context."
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
        total = len(chunks)

        def _files_label(chunk: Chunk) -> str:
            label = ", ".join(chunk.files[:3])
            if len(chunk.files) > 3:
                label += f", +{len(chunk.files) - 3} more"
            return label

        async def run_one(
            idx: int, chunk: Chunk
        ) -> tuple[Chunk, Review | None, BaseException | None]:
            tag = f"[chunk {idx}/{total}]"
            review: Review | None = None
            error: BaseException | None = None
            # Give each independent sub-call the shape of the whole PR so it
            # can reason about cross-file impacts it cannot see directly.
            payload = chunk.content
            if total > 1:
                payload = (
                    render_chunk_preamble(manifest, chunk.files, idx, total)
                    + chunk.content
                )
            try:
                async with sem:
                    t_chunk = time.monotonic()
                    await queue.put(ReviewProgressEvent(
                        f"{tag} started — files: {_files_label(chunk)}"
                    ))
                    try:
                        async for ev in self._ai.stream_review(
                            repo_full_name, pr_number, payload,
                            model=model, timeout=timeout,
                        ):
                            if isinstance(ev, ReviewProgressEvent):
                                # Tag the agent's own live activity lines with
                                # the sub-call they belong to.
                                await queue.put(ReviewProgressEvent(f"{tag} {ev.text}"))
                            elif isinstance(ev, (ReviewChunkEvent, ReviewWarningEvent)):
                                await queue.put(ev)
                            elif isinstance(ev, ReviewResultEvent):
                                review = ev.review
                    except Exception as exc:  # noqa: BLE001 — propagate as warning
                        error = exc
                    elapsed = time.monotonic() - t_chunk
                    if review is not None:
                        await queue.put(ReviewProgressEvent(
                            f"{tag} done — {len(review.findings)} findings — {elapsed:.1f}s"
                        ))
                    elif error is not None:
                        await queue.put(ReviewProgressEvent(
                            f"{tag} failed after {elapsed:.1f}s ({type(error).__name__})"
                        ))
            finally:
                # ALWAYS push the sentinel so the consumer never deadlocks,
                # even if this task is cancelled mid-stream.
                await queue.put((sentinel, chunk, review, error))
            return chunk, review, error

        tasks = [asyncio.create_task(run_one(i, c)) for i, c in enumerate(chunks, start=1)]
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

        findings = _dedupe_findings(findings)
        # Sort findings by priority so P0 appears first, then P1, P2, P3.
        findings.sort(key=lambda f: f.priority)

        merged = Review(summary=merged_summary, findings=findings)

        # Second pass: hand all chunk results back to the provider so it can
        # dedupe semantically (the mechanical dedupe only catches identical
        # file/line/title) and write one coherent summary. Any failure falls
        # back to the mechanical merge — findings are never lost to synthesis.
        succeeded = len(chunks) - failed_count
        if synthesis and succeeded >= 2 and len(findings) >= 2:
            async for event in self._synthesize_merged_review(
                repo_full_name, pr_number, merged, manifest, sub_summaries,
                model=model, timeout=timeout,
            ):
                if isinstance(event, ReviewResultEvent):
                    merged = event.review
                else:
                    yield event

        logger.info(
            "review-service | split done | repo=%s pr=#%d chunks=%d succeeded=%d failed=%d findings=%d",
            repo_full_name, pr_number, len(chunks),
            len(chunks) - failed_count, failed_count, len(merged.findings),
        )
        if finalize:
            self._finalize_review(
                merged, repo_full_name, pr_number, head_sha,
                t0=t0, model=model, diff_chars=len(diff),
                chunks=len(chunks), files=len(files), failed_chunks=failed_count,
            )
        yield ReviewResultEvent(review=merged)

    async def _synthesize_merged_review(
        self,
        repo_full_name: str,
        pr_number: int,
        merged: Review,
        manifest: list[ManifestEntry],
        sub_summaries: list[str],
        *,
        model: str | None,
        timeout: int,
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        """Run the provider's synthesis pass over the mechanically merged review.

        Yields progress/warning/chunk events for the caller to forward, then a
        final ReviewResultEvent ONLY when synthesis produced a usable Review.
        No result event means the caller keeps the mechanical merge — a failed
        or empty synthesis must never lose findings.
        """
        synthesis_input = _build_synthesis_input(manifest, sub_summaries, merged.findings)
        if len(synthesis_input) > _SYNTHESIS_INPUT_MAX_CHARS:
            yield ReviewProgressEvent(
                f"[synthesis] skipped — input too large ({len(synthesis_input)} chars)"
            )
            return

        t_synth = time.monotonic()
        yield ReviewProgressEvent(
            f"[synthesis] consolidating {len(merged.findings)} findings "
            "from the chunk reviews…"
        )
        result: Review | None = None
        try:
            async for ev in self._ai.stream_synthesis(
                repo_full_name, pr_number, synthesis_input,
                model=model, timeout=timeout,
            ):
                if isinstance(ev, ReviewProgressEvent):
                    yield ReviewProgressEvent(f"[synthesis] {ev.text}")
                elif isinstance(ev, (ReviewChunkEvent, ReviewWarningEvent)):
                    yield ev
                elif isinstance(ev, ReviewResultEvent):
                    result = ev.review
        except Exception as exc:  # noqa: BLE001 — synthesis is best-effort
            logger.warning(
                "review-service | synthesis failed | repo=%s pr=#%d error=%s",
                repo_full_name, pr_number, exc,
            )
            yield ReviewWarningEvent([
                f"Synthesis pass failed ({type(exc).__name__}: {exc}); "
                "keeping the mechanically merged review."
            ])
            return

        elapsed = time.monotonic() - t_synth
        if result is None:
            yield ReviewProgressEvent(
                "[synthesis] provider returned no result — keeping the mechanical merge"
            )
            return
        if result.raw_output is not None or not result.findings:
            # Parse fallback, or every candidate dropped — both smell wrong.
            yield ReviewWarningEvent([
                "Synthesis pass returned unusable output; "
                "keeping the mechanically merged review."
            ])
            return

        consolidated = sorted(result.findings, key=lambda f: f.priority)
        yield ReviewProgressEvent(
            f"[synthesis] done — {len(consolidated)} findings "
            f"(from {len(merged.findings)} candidates) — {elapsed:.1f}s"
        )
        logger.info(
            "review-service | synthesis done | repo=%s pr=#%d candidates=%d consolidated=%d",
            repo_full_name, pr_number, len(merged.findings), len(consolidated),
        )
        yield ReviewResultEvent(
            Review(summary=result.summary or merged.summary, findings=consolidated)
        )

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


def _build_synthesis_input(
    manifest: list[ManifestEntry],
    sub_summaries: list[str],
    findings: list[Finding],
) -> str:
    """Assemble the context payload for the synthesis pass."""
    files_block = "\n".join(
        f"- {e.path} (+{e.additions}/-{e.deletions})" for e in manifest
    )
    summaries_block = "\n".join(
        f"- [part {i}] {s}" for i, s in enumerate(sub_summaries, start=1)
    ) or "(none)"
    findings_json = json.dumps(
        [{k: v for k, v in asdict(f).items() if v is not None} for f in findings],
        ensure_ascii=False,
    )
    return (
        "All files changed in this PR:\n" + files_block + "\n\n"
        "Per-part summaries from the independent sub-reviews:\n"
        + summaries_block + "\n\n"
        "Candidate findings (JSON):\n" + findings_json + "\n"
    )


def _dedupe_findings(findings: list[Finding]) -> list[Finding]:
    """Drop duplicate findings produced by overlapping chunk reviews.

    Two findings are duplicates when they target the same file/line and share
    a normalized title; the highest-priority occurrence wins ("P0" < "P1"
    lexicographically, so plain string comparison orders correctly).
    """
    best: dict[tuple, Finding] = {}
    order: list[tuple] = []
    for f in findings:
        key = (f.file, f.line, f.title.strip().lower())
        current = best.get(key)
        if current is None:
            best[key] = f
            order.append(key)
        elif f.priority < current.priority:
            best[key] = f
    return [best[k] for k in order]
