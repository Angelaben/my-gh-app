"""Tests for ReviewService."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.domain.models import Finding, Review
from app.ports.ai_provider import ReviewChunkEvent, ReviewResultEvent
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

        async def mock_stream(repo, pr, diff):
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

        async def mock_stream(repo, pr, diff):
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

        async def mock_stream(repo, pr, diff, model=None):
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
