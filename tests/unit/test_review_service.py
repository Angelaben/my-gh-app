"""Tests for ReviewService."""
import asyncio

import pytest
from unittest.mock import MagicMock

from app.domain.models import Finding, Review
from app.ports.ai_provider import ReviewChunkEvent, ReviewResultEvent, ReviewWarningEvent
from app.services.review_service import ReviewService


def _make_review(summary: str = "LGTM") -> Review:
    return Review(summary=summary, findings=[])


@pytest.fixture
def ai_provider():
    provider = MagicMock()
    return provider


@pytest.fixture
def cache_port():
    cache = MagicMock()
    cache.get_review.return_value = None
    return cache


@pytest.fixture
def vcs_port():
    vcs = MagicMock()
    vcs.get_diff.return_value = "--- a/foo.py\n+++ b/foo.py\n"
    return vcs


@pytest.fixture
def service(ai_provider, cache_port, vcs_port):
    return ReviewService(ai=ai_provider, cache=cache_port, vcs=vcs_port)


class TestGetOrRunReview:
    async def test_returns_cached_review(self, service, cache_port):
        cached = _make_review("cached result")
        cache_port.get_review.return_value = cached
        result = await service.get_or_run_review("acme/backend", 1)
        assert result.summary == "cached result"
        cache_port.get_review.assert_called_once_with("acme/backend", 1)

    async def test_runs_and_caches_when_no_cache(self, service, ai_provider, cache_port, vcs_port):
        review = _make_review("fresh")
        result_event = ReviewResultEvent(review)

        async def mock_stream(repo, pr, diff, model=None, timeout=None):
            yield ReviewChunkEvent("some text")
            yield result_event

        ai_provider.stream_review = mock_stream
        result = await service.get_or_run_review("acme/backend", 1)
        assert result.summary == "fresh"
        cache_port.save_review.assert_called_once_with("acme/backend", 1, review)


class TestRerunReview:
    async def test_skips_cache_and_overwrites(self, service, ai_provider, cache_port, vcs_port):
        review = _make_review("new review")
        result_event = ReviewResultEvent(review)

        async def mock_stream(repo, pr, diff, model=None, timeout=None):
            yield ReviewChunkEvent("text")
            yield result_event

        ai_provider.stream_review = mock_stream
        result = await service.rerun_review("acme/backend", 1)
        assert result.summary == "new review"
        cache_port.get_review.assert_not_called()
        cache_port.save_review.assert_called_once()


class TestStreamReview:
    async def test_yields_chunks_and_result(self, service, ai_provider, cache_port, vcs_port):
        review = _make_review("streamed")
        events = [ReviewChunkEvent("a"), ReviewChunkEvent("b"), ReviewResultEvent(review)]

        async def mock_stream(repo, pr, diff, model=None, timeout=None):
            for e in events:
                yield e

        ai_provider.stream_review = mock_stream
        collected = []
        async for event in service.stream_review("acme/backend", 1):
            collected.append(event)

        assert len(collected) == 3
        assert isinstance(collected[0], ReviewChunkEvent)
        assert isinstance(collected[2], ReviewResultEvent)
        cache_port.save_review.assert_called_once()

    async def test_clear_cache_removes_review(self, service, cache_port):
        service.clear_cache("acme/backend", 1)
        cache_port.clear_review.assert_called_once_with("acme/backend", 1)


class TestStreamReviewSplit:
    async def test_diff_under_threshold_uses_single_call(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "10000")
        vcs_port.get_diff.return_value = "diff --git a/a.py b/a.py\n" + "x" * 100

        review = Review(summary="small", findings=[])
        call_count = 0

        async def mock_stream(repo, pr, diff, model=None, timeout=None):
            nonlocal call_count
            call_count += 1
            yield ReviewChunkEvent("chunk")
            yield ReviewResultEvent(review)

        ai_provider.stream_review = mock_stream
        events = [e async for e in service.stream_review("acme/backend", 1)]

        assert call_count == 1
        warnings = [e for e in events if isinstance(e, ReviewWarningEvent)]
        assert warnings == []
        results = [e for e in events if isinstance(e, ReviewResultEvent)]
        assert len(results) == 1
        assert results[0].review.summary == "small"

    async def test_diff_over_threshold_splits_and_merges(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "200")
        monkeypatch.setenv("REVIEW_MAX_CONCURRENCY", "5")

        # Two files, each ~150 chars, totalling ~300 → must split into 2 chunks.
        diff = (
            "diff --git a/a.py b/a.py\n" + "a" * 130 + "\n"
            "diff --git a/b.py b/b.py\n" + "b" * 130 + "\n"
        )
        vcs_port.get_diff.return_value = diff

        call_diffs: list[str] = []

        async def mock_stream(repo, pr, sub_diff, model=None, timeout=None):
            call_diffs.append(sub_diff)
            # The PR manifest preamble names every file, so discriminate on
            # the actual diff section header.
            tag = "a" if "diff --git a/a.py" in sub_diff else "b"
            yield ReviewChunkEvent(f"text-{tag}")
            yield ReviewResultEvent(
                Review(
                    summary=f"summary-{tag}",
                    findings=[Finding(priority="P1", title=f"finding-{tag}", description="d")],
                )
            )

        ai_provider.stream_review = mock_stream
        events = [e async for e in service.stream_review("acme/backend", 1)]

        # Two parallel sub-reviews fired.
        assert len(call_diffs) == 2

        # Initial split warning emitted before any chunk text.
        warnings = [e for e in events if isinstance(e, ReviewWarningEvent)]
        assert len(warnings) >= 1
        first_warning_text = " ".join(warnings[0].lines)
        assert "split into 2 chunks" in first_warning_text
        assert "2 files" in first_warning_text

        # Both sub-streams' chunk events surfaced to the caller.
        chunk_texts = [e.text for e in events if isinstance(e, ReviewChunkEvent)]
        assert "text-a" in chunk_texts
        assert "text-b" in chunk_texts

        # Final merged review: both findings, structured summary.
        results = [e for e in events if isinstance(e, ReviewResultEvent)]
        assert len(results) == 1
        merged = results[0].review
        titles = sorted(f.title for f in merged.findings)
        assert titles == ["finding-a", "finding-b"]
        assert "Reviewed across 2 chunks" in merged.summary
        assert "2 files" in merged.summary

    async def test_split_path_caches_merged_review(
        self, service, ai_provider, cache_port, vcs_port, monkeypatch
    ):
        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "100")
        diff = (
            "diff --git a/a.py b/a.py\n" + "a" * 80 + "\n"
            "diff --git a/b.py b/b.py\n" + "b" * 80 + "\n"
        )
        vcs_port.get_diff.return_value = diff

        async def mock_stream(repo, pr, sub_diff, model=None, timeout=None):
            yield ReviewResultEvent(Review(summary="s", findings=[]))

        ai_provider.stream_review = mock_stream
        _ = [e async for e in service.stream_review("acme/backend", 1)]

        cache_port.save_review.assert_called_once()
        cached = cache_port.save_review.call_args[0][2]
        assert isinstance(cached, Review)
        assert "Reviewed across" in cached.summary

    async def test_one_chunk_failure_emits_warning_and_preserves_others(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        from app.domain.exceptions import ProviderError

        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "100")
        diff = (
            "diff --git a/a.py b/a.py\n" + "a" * 80 + "\n"
            "diff --git a/b.py b/b.py\n" + "b" * 80 + "\n"
        )
        vcs_port.get_diff.return_value = diff

        async def mock_stream(repo, pr, sub_diff, model=None, timeout=None):
            if "diff --git a/a.py" in sub_diff:
                raise ProviderError("claude exited with code 124")
            yield ReviewResultEvent(
                Review(
                    summary="b ok",
                    findings=[Finding(priority="P2", title="from-b", description="d")],
                )
            )

        ai_provider.stream_review = mock_stream
        events = [e async for e in service.stream_review("acme/backend", 1)]

        warnings = [e for e in events if isinstance(e, ReviewWarningEvent)]
        flat = " ".join(line for w in warnings for line in w.lines)
        assert "Chunk failed" in flat
        assert "a.py" in flat
        assert "ProviderError" in flat

        results = [e for e in events if isinstance(e, ReviewResultEvent)]
        assert len(results) == 1
        merged = results[0].review
        assert [f.title for f in merged.findings] == ["from-b"]
        assert "1 failed" in merged.summary

    async def test_all_chunks_fail_yields_empty_review_with_warnings(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        from app.domain.exceptions import ProviderError

        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "100")
        diff = (
            "diff --git a/a.py b/a.py\n" + "a" * 80 + "\n"
            "diff --git a/b.py b/b.py\n" + "b" * 80 + "\n"
        )
        vcs_port.get_diff.return_value = diff

        async def mock_stream(repo, pr, sub_diff, model=None, timeout=None):
            # The yield below is unreachable but required to make this an
            # async generator (so it matches the AIProvider.stream_review
            # signature). Always raises before yielding.
            if False:
                yield
            raise ProviderError("boom")

        ai_provider.stream_review = mock_stream
        events = [e async for e in service.stream_review("acme/backend", 1)]

        warnings = [e for e in events if isinstance(e, ReviewWarningEvent)]
        chunk_failure_warnings = [
            w for w in warnings if any("Chunk failed" in line for line in w.lines)
        ]
        assert len(chunk_failure_warnings) == 2

        results = [e for e in events if isinstance(e, ReviewResultEvent)]
        assert len(results) == 1
        assert results[0].review.findings == []
        assert "2 failed" in results[0].review.summary

    async def test_merged_findings_sorted_by_priority(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "200")
        monkeypatch.setenv("REVIEW_MAX_CONCURRENCY", "5")

        diff = (
            "diff --git a/a.py b/a.py\n" + "a" * 130 + "\n"
            "diff --git a/b.py b/b.py\n" + "b" * 130 + "\n"
        )
        vcs_port.get_diff.return_value = diff

        async def mock_stream(repo, pr, sub_diff, model=None, timeout=None):
            if "diff --git a/a.py" in sub_diff:
                yield ReviewResultEvent(
                    Review(summary="a", findings=[
                        Finding(priority="P2", title="low-a", description="d"),
                        Finding(priority="P0", title="critical-a", description="d"),
                    ])
                )
            else:
                yield ReviewResultEvent(
                    Review(summary="b", findings=[
                        Finding(priority="P3", title="info-b", description="d"),
                        Finding(priority="P1", title="medium-b", description="d"),
                    ])
                )

        ai_provider.stream_review = mock_stream
        events = [e async for e in service.stream_review("acme/backend", 1)]

        results = [e for e in events if isinstance(e, ReviewResultEvent)]
        assert len(results) == 1
        priorities = [f.priority for f in results[0].review.findings]
        assert priorities == ["P0", "P1", "P2", "P3"]

    async def test_concurrency_cap_is_respected(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "100")
        monkeypatch.setenv("REVIEW_MAX_CONCURRENCY", "2")

        # 5 files, each ~80 chars → 5 chunks (each file is its own chunk
        # because two ~80-char files would exceed the 100-char threshold).
        diff = "".join(
            f"diff --git a/f{i}.py b/f{i}.py\n" + "x" * 80 + "\n"
            for i in range(5)
        )
        vcs_port.get_diff.return_value = diff

        in_flight = 0
        max_in_flight = 0
        lock = asyncio.Lock()

        async def mock_stream(repo, pr, sub_diff, model=None, timeout=None):
            nonlocal in_flight, max_in_flight
            async with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            try:
                # Hold the call long enough for parallel scheduling to settle.
                await asyncio.sleep(0.01)
                yield ReviewResultEvent(Review(summary="ok", findings=[]))
            finally:
                async with lock:
                    in_flight -= 1

        ai_provider.stream_review = mock_stream
        _ = [e async for e in service.stream_review("acme/backend", 1)]

        assert max_in_flight <= 2, f"semaphore breached: max_in_flight={max_in_flight}"
        # Sanity: we did actually overlap (otherwise the cap test is vacuous).
        assert max_in_flight >= 2


class TestChunkSharedContext:
    """Split sub-calls carry a shared PR manifest so they aren't blind to
    the rest of the change set."""

    async def test_each_chunk_receives_full_pr_manifest(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "200")
        diff = (
            "diff --git a/a.py b/a.py\n"
            "+new a line\n" + "a" * 130 + "\n"
            "diff --git a/b.py b/b.py\n"
            "-old b line\n" + "b" * 130 + "\n"
        )
        vcs_port.get_diff.return_value = diff
        call_diffs: list[str] = []

        async def mock_stream(repo, pr, sub_diff, model=None, timeout=None):
            call_diffs.append(sub_diff)
            yield ReviewResultEvent(Review(summary="s", findings=[]))

        ai_provider.stream_review = mock_stream
        _ = [e async for e in service.stream_review("acme/backend", 1)]

        assert len(call_diffs) == 2
        for sub_diff in call_diffs:
            # Every sub-call sees the complete file list with change stats.
            assert "All files changed in this PR:" in sub_diff
            assert "- a.py (+1/-0)" in sub_diff
            assert "- b.py (+0/-1)" in sub_diff
            # Exactly one file is attached; the other is flagged as elsewhere.
            assert sub_diff.count("(in this part)") == 1
            assert sub_diff.count("(in another part)") == 1
            # The preamble precedes the actual diff section.
            assert "[DIFF PART FOLLOWS]" in sub_diff
            assert sub_diff.index("[PR CONTEXT") < sub_diff.index("diff --git ")

        # Each part is numbered.
        parts = sorted(d[d.index("part ") : d.index(" of a large")] for d in call_diffs)
        assert parts == ["part 1/2", "part 2/2"]

    async def test_single_call_path_has_no_preamble(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "10000")
        vcs_port.get_diff.return_value = "diff --git a/a.py b/a.py\n+x\n"
        seen: list[str] = []

        async def mock_stream(repo, pr, sub_diff, model=None, timeout=None):
            seen.append(sub_diff)
            yield ReviewResultEvent(Review(summary="s", findings=[]))

        ai_provider.stream_review = mock_stream
        _ = [e async for e in service.stream_review("acme/backend", 1)]

        assert len(seen) == 1
        assert "[PR CONTEXT" not in seen[0]
        assert seen[0].startswith("diff --git ")


def _split_diff() -> str:
    """Two-file diff that splits into 2 chunks at REVIEW_DIFF_MAX_CHARS=200."""
    return (
        "diff --git a/a.py b/a.py\n" + "a" * 130 + "\n"
        "diff --git a/b.py b/b.py\n" + "b" * 130 + "\n"
    )


def _two_finding_chunk_stream():
    """stream_review mock: each chunk reports one finding."""
    async def mock_stream(repo, pr, sub_diff, model=None, timeout=None):
        tag = "a" if "diff --git a/a.py" in sub_diff else "b"
        yield ReviewResultEvent(
            Review(
                summary=f"summary-{tag}",
                findings=[Finding(priority="P1", title=f"finding-{tag}", description="d")],
            )
        )
    return mock_stream


class TestSynthesisPass:
    """Second AI pass consolidating split-review findings."""

    async def test_synthesis_result_replaces_mechanical_merge(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "200")
        monkeypatch.setenv("REVIEW_SYNTHESIS", "1")
        vcs_port.get_diff.return_value = _split_diff()
        ai_provider.stream_review = _two_finding_chunk_stream()

        synthesis_inputs: list[str] = []

        async def mock_synthesis(repo, pr, synthesis_input, model=None, timeout=None):
            synthesis_inputs.append(synthesis_input)
            yield ReviewResultEvent(
                Review(
                    summary="One coherent summary.",
                    findings=[Finding(priority="P0", title="consolidated", description="d")],
                )
            )

        ai_provider.stream_synthesis = mock_synthesis
        events = [e async for e in service.stream_review("acme/backend", 1)]

        results = [e for e in events if isinstance(e, ReviewResultEvent)]
        assert len(results) == 1
        assert results[0].review.summary == "One coherent summary."
        assert [f.title for f in results[0].review.findings] == ["consolidated"]

        # The synthesis input carries everything needed to consolidate.
        assert len(synthesis_inputs) == 1
        payload = synthesis_inputs[0]
        assert "All files changed in this PR:" in payload
        assert "- a.py" in payload and "- b.py" in payload
        assert "summary-a" in payload and "summary-b" in payload
        assert '"finding-a"' in payload and '"finding-b"' in payload

    async def test_synthesis_failure_keeps_mechanical_merge(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        from app.domain.exceptions import ProviderError

        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "200")
        vcs_port.get_diff.return_value = _split_diff()
        ai_provider.stream_review = _two_finding_chunk_stream()

        async def mock_synthesis(repo, pr, synthesis_input, model=None, timeout=None):
            if False:
                yield
            raise ProviderError("synthesis boom")

        ai_provider.stream_synthesis = mock_synthesis
        events = [e async for e in service.stream_review("acme/backend", 1)]

        results = [e for e in events if isinstance(e, ReviewResultEvent)]
        assert sorted(f.title for f in results[0].review.findings) == [
            "finding-a", "finding-b",
        ]
        flat = " ".join(line for w in events if isinstance(w, ReviewWarningEvent) for line in w.lines)
        assert "Synthesis pass failed" in flat

    async def test_synthesis_empty_result_keeps_mechanical_merge(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "200")
        vcs_port.get_diff.return_value = _split_diff()
        ai_provider.stream_review = _two_finding_chunk_stream()

        async def mock_synthesis(repo, pr, synthesis_input, model=None, timeout=None):
            yield ReviewResultEvent(Review(summary="dropped everything", findings=[]))

        ai_provider.stream_synthesis = mock_synthesis
        events = [e async for e in service.stream_review("acme/backend", 1)]

        results = [e for e in events if isinstance(e, ReviewResultEvent)]
        assert sorted(f.title for f in results[0].review.findings) == [
            "finding-a", "finding-b",
        ]
        flat = " ".join(line for w in events if isinstance(w, ReviewWarningEvent) for line in w.lines)
        assert "unusable output" in flat

    async def test_synthesis_disabled_by_setting(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "200")
        monkeypatch.setenv("REVIEW_SYNTHESIS", "0")
        vcs_port.get_diff.return_value = _split_diff()
        ai_provider.stream_review = _two_finding_chunk_stream()

        async def mock_synthesis(repo, pr, synthesis_input, model=None, timeout=None):
            raise AssertionError("synthesis must not run when disabled")
            yield

        ai_provider.stream_synthesis = mock_synthesis
        events = [e async for e in service.stream_review("acme/backend", 1)]
        results = [e for e in events if isinstance(e, ReviewResultEvent)]
        assert len(results[0].review.findings) == 2

    async def test_synthesis_skipped_when_only_one_chunk_succeeds(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        from app.domain.exceptions import ProviderError

        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "200")
        vcs_port.get_diff.return_value = _split_diff()

        async def mock_stream(repo, pr, sub_diff, model=None, timeout=None):
            if "diff --git a/a.py" in sub_diff:
                raise ProviderError("chunk boom")
            yield ReviewResultEvent(
                Review(summary="b", findings=[
                    Finding(priority="P1", title="x", description="d"),
                    Finding(priority="P2", title="y", description="d"),
                ])
            )

        async def mock_synthesis(repo, pr, synthesis_input, model=None, timeout=None):
            raise AssertionError("synthesis needs >= 2 successful chunks")
            yield

        ai_provider.stream_review = mock_stream
        ai_provider.stream_synthesis = mock_synthesis
        events = [e async for e in service.stream_review("acme/backend", 1)]
        results = [e for e in events if isinstance(e, ReviewResultEvent)]
        assert len(results[0].review.findings) == 2


class TestIgnoredFiles:
    async def test_ignored_files_are_excluded_and_warned(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "10000")
        vcs_port.get_diff.return_value = (
            "diff --git a/app.py b/app.py\n" + "x" * 50 + "\n"
            "diff --git a/package-lock.json b/package-lock.json\n" + "y" * 50 + "\n"
        )
        seen_diffs: list[str] = []

        async def mock_stream(repo, pr, diff, model=None, timeout=None):
            seen_diffs.append(diff)
            yield ReviewResultEvent(_make_review("ok"))

        ai_provider.stream_review = mock_stream
        events = [e async for e in service.stream_review("acme/backend", 1)]

        assert len(seen_diffs) == 1
        assert "app.py" in seen_diffs[0]
        assert "package-lock.json" not in seen_diffs[0]
        warnings = [e for e in events if isinstance(e, ReviewWarningEvent)]
        flat = " ".join(line for w in warnings for line in w.lines)
        assert "package-lock.json" in flat

    async def test_all_files_ignored_short_circuits(
        self, service, ai_provider, vcs_port, cache_port
    ):
        vcs_port.get_diff.return_value = (
            "diff --git a/uv.lock b/uv.lock\n" + "y" * 50 + "\n"
        )
        ai_provider.stream_review = MagicMock(
            side_effect=AssertionError("AI must not be called")
        )
        events = [e async for e in service.stream_review("acme/backend", 1)]
        results = [e for e in events if isinstance(e, ReviewResultEvent)]
        assert len(results) == 1
        assert results[0].review.findings == []
        assert "nothing to review" in results[0].review.summary
        cache_port.save_review.assert_called_once()

    async def test_custom_globs_override_defaults(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        monkeypatch.setenv("REVIEW_IGNORE_GLOBS", "")  # disable filtering
        vcs_port.get_diff.return_value = (
            "diff --git a/package-lock.json b/package-lock.json\n" + "y" * 50 + "\n"
        )
        seen_diffs: list[str] = []

        async def mock_stream(repo, pr, diff, model=None, timeout=None):
            seen_diffs.append(diff)
            yield ReviewResultEvent(_make_review("ok"))

        ai_provider.stream_review = mock_stream
        _ = [e async for e in service.stream_review("acme/backend", 1)]
        assert "package-lock.json" in seen_diffs[0]


class TestDedupeFindings:
    def test_merged_duplicate_findings_are_deduped(self):
        from app.services.review_service import _dedupe_findings

        findings = [
            Finding(priority="P2", title="SQL injection", description="a", file="db.py", line=10),
            Finding(priority="P0", title="sql injection ", description="b", file="db.py", line=10),
            Finding(priority="P2", title="SQL injection", description="c", file="other.py", line=10),
        ]
        deduped = _dedupe_findings(findings)
        assert len(deduped) == 2
        assert deduped[0].priority == "P0"  # highest severity kept
        assert deduped[1].file == "other.py"

    def test_distinct_findings_preserved(self):
        from app.services.review_service import _dedupe_findings

        findings = [
            Finding(priority="P1", title="A", description="", file="x.py", line=1),
            Finding(priority="P1", title="B", description="", file="x.py", line=1),
            Finding(priority="P1", title="A", description="", file="x.py", line=2),
        ]
        assert len(_dedupe_findings(findings)) == 3


class TestSplitProgressEvents:
    """Agent sub-call lifecycle is surfaced as ReviewProgressEvent in split mode."""

    async def test_chunk_lifecycle_progress_emitted(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        from app.ports.ai_provider import ReviewProgressEvent

        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "100")
        vcs_port.get_diff.return_value = (
            "diff --git a/a.py b/a.py\n" + "a" * 80 + "\n"
            "diff --git a/b.py b/b.py\n" + "b" * 80 + "\n"
        )

        async def mock_stream(repo, pr, sub_diff, model=None, timeout=None):
            yield ReviewProgressEvent("> Reading context…")
            yield ReviewResultEvent(
                Review(summary="s", findings=[Finding(priority="P2", title="t", description="d")])
            )

        ai_provider.stream_review = mock_stream
        events = [e async for e in service.stream_review("acme/backend", 1)]
        progress = [e.text for e in events if isinstance(e, ReviewProgressEvent)]

        assert sum("started — files:" in p for p in progress) == 2
        assert sum("done — 1 findings" in p for p in progress) == 2
        # The agent's own live activity lines are tagged with their sub-call.
        tagged = [p for p in progress if "> Reading context…" in p]
        assert len(tagged) == 2
        assert all(p.startswith("[chunk ") for p in tagged)

    async def test_failed_chunk_emits_failure_progress(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        from app.domain.exceptions import ProviderError
        from app.ports.ai_provider import ReviewProgressEvent

        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "100")
        vcs_port.get_diff.return_value = (
            "diff --git a/a.py b/a.py\n" + "a" * 80 + "\n"
            "diff --git a/b.py b/b.py\n" + "b" * 80 + "\n"
        )

        async def mock_stream(repo, pr, sub_diff, model=None, timeout=None):
            if "diff --git a/a.py" in sub_diff:
                raise ProviderError("boom")
            yield ReviewResultEvent(Review(summary="ok", findings=[]))

        ai_provider.stream_review = mock_stream
        events = [e async for e in service.stream_review("acme/backend", 1)]
        progress = [e.text for e in events if isinstance(e, ReviewProgressEvent)]
        assert any("failed after" in p and "ProviderError" in p for p in progress)


class TestReviewProvenance:
    """Head-SHA stamping on fresh reviews and the staleness check."""

    async def test_fresh_review_is_stamped_with_head_sha(
        self, service, ai_provider, cache_port, vcs_port
    ):
        vcs_port.get_pr_head_sha.return_value = "abc123"
        review = _make_review("fresh")

        async def mock_stream(repo, pr, diff, model=None, timeout=None):
            yield ReviewResultEvent(review)

        ai_provider.stream_review = mock_stream
        result = await service.get_or_run_review("acme/backend", 1)
        assert result.head_sha == "abc123"
        assert result.created_at is not None

    async def test_split_review_is_stamped_with_head_sha(
        self, service, ai_provider, vcs_port, monkeypatch
    ):
        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "100")
        vcs_port.get_pr_head_sha.return_value = "def456"
        vcs_port.get_diff.return_value = (
            "diff --git a/a.py b/a.py\n" + "a" * 80 + "\n"
            "diff --git a/b.py b/b.py\n" + "b" * 80 + "\n"
        )

        async def mock_stream(repo, pr, sub_diff, model=None, timeout=None):
            yield ReviewResultEvent(Review(summary="s", findings=[]))

        ai_provider.stream_review = mock_stream
        events = [e async for e in service.stream_review("acme/backend", 1)]
        merged = [e for e in events if isinstance(e, ReviewResultEvent)][0].review
        assert merged.head_sha == "def456"

    async def test_sha_lookup_failure_does_not_break_review(
        self, service, ai_provider, vcs_port
    ):
        vcs_port.get_pr_head_sha.side_effect = RuntimeError("gh offline")
        review = _make_review("ok")

        async def mock_stream(repo, pr, diff, model=None, timeout=None):
            yield ReviewResultEvent(review)

        ai_provider.stream_review = mock_stream
        result = await service.get_or_run_review("acme/backend", 1)
        assert result.summary == "ok"
        assert result.head_sha is None

    def test_stale_when_sha_differs(self, service, vcs_port):
        vcs_port.get_pr_head_sha.return_value = "new-sha"
        review = Review(summary="s", findings=[], head_sha="old-sha")
        assert service.is_review_stale("acme/backend", 1, review) is True

    def test_not_stale_when_sha_matches(self, service, vcs_port):
        vcs_port.get_pr_head_sha.return_value = "same"
        review = Review(summary="s", findings=[], head_sha="same")
        assert service.is_review_stale("acme/backend", 1, review) is False

    def test_not_stale_when_review_has_no_sha(self, service, vcs_port):
        review = Review(summary="s", findings=[])
        assert service.is_review_stale("acme/backend", 1, review) is False
        vcs_port.get_pr_head_sha.assert_not_called()

    def test_not_stale_when_lookup_fails(self, service, vcs_port):
        vcs_port.get_pr_head_sha.side_effect = RuntimeError("gh offline")
        review = Review(summary="s", findings=[], head_sha="old-sha")
        assert service.is_review_stale("acme/backend", 1, review) is False


class TestSettingsEnv:
    """Env resolution in settings_store (replaces the old _read_int_env helper)."""

    def test_env_int_parsing(self, monkeypatch):
        from app.services import settings_store as ss
        monkeypatch.delenv("MY_TEST_VAR", raising=False)
        assert ss._env_int("MY_TEST_VAR", 42) == 42
        monkeypatch.setenv("MY_TEST_VAR", "")
        assert ss._env_int("MY_TEST_VAR", 42) == 42
        monkeypatch.setenv("MY_TEST_VAR", "abc")
        assert ss._env_int("MY_TEST_VAR", 42) == 42
        monkeypatch.setenv("MY_TEST_VAR", "0")
        assert ss._env_int("MY_TEST_VAR", 42) == 42
        monkeypatch.setenv("MY_TEST_VAR", "-5")
        assert ss._env_int("MY_TEST_VAR", 42) == 42
        monkeypatch.setenv("MY_TEST_VAR", "100")
        assert ss._env_int("MY_TEST_VAR", 42) == 100

    def test_review_settings_reflect_env(self, tmp_path, monkeypatch):
        from app.services import settings_store as ss
        monkeypatch.setattr(ss, "_SETTINGS_PATH", tmp_path / "settings.json")
        monkeypatch.setenv("REVIEW_DIFF_MAX_CHARS", "12345")
        monkeypatch.setenv("REVIEW_MAX_CONCURRENCY", "7")
        s = ss.get_review_settings()
        assert s["review_diff_max_chars"] == 12345
        assert s["review_max_concurrency"] == 7


def _make_pr(number: int = 1, updated_at: str = "2026-01-01T00:00:00Z", is_draft: bool = False):
    from app.domain.models import PR
    return PR(
        number=number, title="t", author="a", branch="b", base_branch="main",
        additions=1, deletions=0, updated_at=updated_at, url="u", is_draft=is_draft,
    )


class TestNeedsReviewEstimate:
    def test_never_reviewed_needs_review(self, service):
        assert service.needs_review_estimate(_make_pr(), None) is True

    def test_review_without_head_sha_skipped(self, service):
        review = Review(summary="s", findings=[], head_sha=None, created_at="2026-01-01T00:00:00Z")
        assert service.needs_review_estimate(_make_pr(), review) is False

    def test_up_to_date_when_untouched_since_review(self, service):
        review = Review(
            summary="s", findings=[], head_sha="abc123", created_at="2026-01-02T00:00:00Z",
        )
        pr = _make_pr(updated_at="2026-01-01T00:00:00Z")  # older than the review
        assert service.needs_review_estimate(pr, review) is False

    def test_updated_after_review_needs_review(self, service):
        review = Review(
            summary="s", findings=[], head_sha="abc123", created_at="2026-01-01T00:00:00Z",
        )
        pr = _make_pr(updated_at="2026-01-03T00:00:00Z")  # newer than the review
        assert service.needs_review_estimate(pr, review) is True

    def test_unparseable_timestamps_conservatively_need_review(self, service):
        review = Review(summary="s", findings=[], head_sha="abc123", created_at="not-a-date")
        assert service.needs_review_estimate(_make_pr(updated_at="also-bad"), review) is True
