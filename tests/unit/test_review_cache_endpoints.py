"""Tests for the read-only cached-review endpoints.

GET /api/review/{owner}/{repo}/{pr_number} and GET /api/reviews/{owner}/{repo}
restore reviews after a page reload. They must return the persisted review
without ever running a fresh one.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.adapters.cache.json_file_cache import JsonFileCache
from app.domain.models import Finding, Review
from app.main import app, get_review_service
from app.services.review_service import ReviewService


@pytest.fixture
def svc(tmp_path: Path) -> ReviewService:
    """A ReviewService backed by a temp cache; ai/vcs are mocks so any attempt
    to run a review (rather than read the cache) is observable and fails loud."""
    cache = JsonFileCache(cache_dir=tmp_path)
    return ReviewService(ai=MagicMock(), cache=cache, vcs=MagicMock())


@pytest.fixture
def client(svc: ReviewService):
    app.dependency_overrides[get_review_service] = lambda: svc
    yield TestClient(app)
    app.dependency_overrides.pop(get_review_service, None)


class TestGetCachedReview:
    def test_204_when_not_cached(self, client: TestClient, svc: ReviewService):
        resp = client.get("/api/review/acme/backend/42")
        assert resp.status_code == 204
        svc._ai.stream_review.assert_not_called()

    def test_returns_cached_review_with_stale_flag(self, client: TestClient, svc: ReviewService):
        svc._cache.save_review(
            "acme/backend", 42,
            Review(summary="LGTM", findings=[Finding(priority="P2", title="Style", description="d")]),
        )
        resp = client.get("/api/review/acme/backend/42")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "LGTM"
        assert len(data["findings"]) == 1
        assert data["stale"] is False  # no head_sha → never stale, no vcs call
        svc._ai.stream_review.assert_not_called()


class TestListCachedReviews:
    def test_empty_list_when_none_cached(self, client: TestClient, svc: ReviewService):
        resp = client.get("/api/reviews/acme/backend")
        assert resp.status_code == 200
        assert resp.json() == {"reviews": []}

    def test_lists_all_cached_reviews_with_pr_numbers(self, client: TestClient, svc: ReviewService):
        svc._cache.save_review("acme/backend", 3, Review(summary="three", findings=[]))
        svc._cache.save_review("acme/backend", 7, Review(summary="seven", findings=[]))
        resp = client.get("/api/reviews/acme/backend")
        assert resp.status_code == 200
        reviews = {r["pr_number"]: r for r in resp.json()["reviews"]}
        assert reviews.keys() == {3, 7}
        assert reviews[3]["summary"] == "three"
        assert reviews[7]["summary"] == "seven"

    def test_never_runs_a_review(self, client: TestClient, svc: ReviewService):
        client.get("/api/reviews/acme/backend")
        svc._ai.stream_review.assert_not_called()
        svc._vcs.get_diff.assert_not_called()
