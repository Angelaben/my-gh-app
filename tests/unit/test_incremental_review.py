"""Tests for incremental review + diff hunk extraction."""
from unittest.mock import MagicMock

import pytest

from app.domain.models import Finding, Review
from app.ports.ai_provider import ReviewResultEvent
from app.services._diff_splitter import extract_hunk_rows
from app.services.review_service import ReviewService


@pytest.fixture
def ai_provider():
    return MagicMock()


@pytest.fixture
def cache_port():
    return MagicMock()


@pytest.fixture
def vcs_port():
    return MagicMock()


@pytest.fixture
def service(ai_provider, cache_port, vcs_port):
    return ReviewService(ai=ai_provider, cache=cache_port, vcs=vcs_port)


class TestIncrementalReview:
    async def test_no_cache_falls_back_to_full(self, service, cache_port, vcs_port, ai_provider):
        cache_port.get_review.return_value = None
        vcs_port.get_diff.return_value = "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n+x\n"
        vcs_port.get_pr_head_sha.return_value = "headsha"

        async def mock_stream(repo, pr, diff, model=None, timeout=None):
            yield ReviewResultEvent(Review(summary="full", findings=[]))

        ai_provider.stream_review = mock_stream
        result = await service.incremental_review("acme/repo", 1)
        assert result["base_sha"] is None
        assert result["review"].summary == "full"

    async def test_no_changes_returns_cached(self, service, cache_port, vcs_port):
        cached = Review(summary="cached", findings=[], head_sha="same")
        cache_port.get_review.return_value = cached
        vcs_port.get_pr_head_sha.return_value = "same"
        result = await service.incremental_review("acme/repo", 1)
        assert result["no_changes"] is True
        assert result["review"] is cached
        assert result["carried"] == 0

    async def test_merges_carried_and_new(self, service, cache_port, vcs_port, ai_provider):
        cached = Review(
            summary="old",
            findings=[
                Finding(priority="P1", title="old-untouched", description="d", file="keep.py", line=1),
                Finding(priority="P2", title="old-in-changed", description="d", file="changed.py", line=5),
            ],
            head_sha="base",
        )
        cache_port.get_review.return_value = cached
        vcs_port.get_pr_head_sha.return_value = "head"
        vcs_port.get_diff_between_shas.return_value = (
            "diff --git a/changed.py b/changed.py\n@@ -1,1 +1,1 @@\n+new\n"
        )

        async def mock_stream(repo, pr, diff, model=None, timeout=None):
            yield ReviewResultEvent(Review(summary="new", findings=[
                Finding(priority="P0", title="new-finding", description="d", file="changed.py", line=1),
            ]))

        ai_provider.stream_review = mock_stream
        result = await service.incremental_review("acme/repo", 1)
        assert result["no_changes"] is False
        assert result["base_sha"] == "base"
        assert result["head_sha"] == "head"
        assert result["changed_files"] == ["changed.py"]
        # old-untouched carried over, new-finding added, old-in-changed dropped.
        assert sorted(f.title for f in result["review"].findings) == ["new-finding", "old-untouched"]
        assert result["carried"] == 1
        assert result["new"] == 1


class TestExtractHunkRows:
    def test_finds_target_line(self):
        content = (
            "diff --git a/f.py b/f.py\n"
            "index abc..def 100644\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            " line2\n"
            "+added3\n"
            " line4\n"
        )
        rows, found = extract_hunk_rows(content, 3, context=2)
        assert found is True
        target = [r for r in rows if r.new_line == 3][0]
        assert target.sign == "+"
        assert target.text == "added3"

    def test_returns_not_found_for_line_outside_diff(self):
        content = "diff --git a/f.py b/f.py\n@@ -1,1 +1,1 @@\n+x\n"
        rows, found = extract_hunk_rows(content, 999, context=2)
        assert found is False
        assert rows == []
