"""Tests for CommentService."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.domain.models import Comment
from app.services.comment_service import CommentService


@pytest.fixture
def ai_provider():
    return MagicMock()


@pytest.fixture
def vcs_port():
    vcs = MagicMock()
    vcs.get_comments.return_value = {
        "comments": [
            {"author": {"login": "alice"}, "body": "LGTM", "created_at": "2024-01-01T00:00:00Z"},
        ],
        "reviews": [],
        "review_comments": [],
    }
    return vcs


@pytest.fixture
def service(ai_provider, vcs_port):
    return CommentService(ai=ai_provider, vcs=vcs_port)


class TestGetComments:
    def test_normalizes_author_object(self, service, vcs_port):
        result = service.get_comments("acme/backend", 1)
        assert result["comments"][0]["author"] == "alice"

    def test_includes_review_bodies(self, service, vcs_port):
        vcs_port.get_comments.return_value = {
            "comments": [],
            "reviews": [{"author": {"login": "bob"}, "body": "Needs changes", "created_at": "2024-01-01T00:00:00Z"}],
            "review_comments": [],
        }
        result = service.get_comments("acme/backend", 1)
        assert len(result["comments"]) == 1
        assert result["comments"][0]["author"] == "bob"

    def test_includes_inline_review_comments(self, service, vcs_port):
        vcs_port.get_comments.return_value = {
            "comments": [],
            "reviews": [],
            "review_comments": [
                {"author": {"login": "carol"}, "body": "typo here", "path": "app/main.py", "line": 42, "created_at": "2024-01-01T00:00:00Z"}
            ],
        }
        result = service.get_comments("acme/backend", 1)
        assert len(result["comments"]) == 1
        assert result["comments"][0]["_inline"] is True

    def test_does_not_expose_raw_data(self, service, vcs_port):
        result = service.get_comments("acme/backend", 1)
        assert "raw" not in result


class TestAnalyzeComments:
    async def test_calls_ai_with_normalized_comments(self, service, ai_provider):
        ai_provider.analyze_comments = AsyncMock(return_value=[
            {"author": "alice", "criticality": "P2", "valid": True, "interest": "low", "summary": "ok"}
        ])
        result = await service.analyze_comments("acme/backend", 1)
        assert result["analysis"][0]["author"] == "alice"
        ai_provider.analyze_comments.assert_called_once()

    async def test_returns_empty_when_no_comments(self, service, vcs_port, ai_provider):
        vcs_port.get_comments.return_value = {
            "comments": [], "reviews": [], "review_comments": []
        }
        ai_provider.analyze_comments = AsyncMock(return_value=[])
        result = await service.analyze_comments("acme/backend", 1)
        assert result["analysis"] == []
