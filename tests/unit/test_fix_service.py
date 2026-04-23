"""Tests for FixService."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.domain.models import FixResult
from app.ports.ai_provider import FixChunkEvent
from app.services.fix_service import FixService


@pytest.fixture
def ai_provider():
    return MagicMock()


@pytest.fixture
def vcs_port():
    vcs = MagicMock()
    vcs.get_pr_head_branch.return_value = "feature/fix"
    return vcs


@pytest.fixture
def worktree_port():
    wt = MagicMock()
    wt.create.return_value = "/tmp/worktree"
    wt.worktree_path.return_value = "/tmp/worktree"
    wt.has_changes.return_value = True
    wt.get_staged_diff.return_value = "--- a/foo.py\n+++ b/foo.py\n"
    return wt


@pytest.fixture
def service(ai_provider, vcs_port, worktree_port):
    return FixService(ai=ai_provider, vcs=vcs_port, worktree=worktree_port)


class TestStreamFix:
    async def test_yields_status_and_chunks(self, service, ai_provider, vcs_port, worktree_port):
        async def mock_fix(*args):
            yield FixChunkEvent("some output")

        ai_provider.stream_fix = mock_fix

        events = []
        async for event in service.stream_fix("acme/backend", 1, "fix this"):
            events.append(event)

        event_types = [e["type"] for e in events]
        assert "status" in event_types
        assert "chunk" in event_types
        assert "result" in event_types
        assert "done" in event_types

    async def test_result_has_changes(self, service, ai_provider, worktree_port):
        async def mock_fix(*args):
            yield FixChunkEvent("output")

        ai_provider.stream_fix = mock_fix
        worktree_port.has_changes.return_value = True
        worktree_port.get_staged_diff.return_value = "diff content"

        events = []
        async for event in service.stream_fix("acme/backend", 1, "fix this"):
            events.append(event)

        result = next(e for e in events if e["type"] == "result")
        assert result["has_changes"] is True
        assert "diff" in result

    async def test_result_no_changes(self, service, ai_provider, worktree_port):
        async def mock_fix(*args):
            yield FixChunkEvent("output")

        ai_provider.stream_fix = mock_fix
        worktree_port.has_changes.return_value = False

        events = []
        async for event in service.stream_fix("acme/backend", 1, "fix this"):
            events.append(event)

        result = next(e for e in events if e["type"] == "result")
        assert result["has_changes"] is False

    async def test_error_yields_error_and_done(self, service, vcs_port):
        vcs_port.get_pr_head_branch.side_effect = Exception("network error")

        events = []
        async for event in service.stream_fix("acme/backend", 1, "fix this"):
            events.append(event)

        types = [e["type"] for e in events]
        assert "error" in types
        assert "done" in types


class TestPushFix:
    async def test_commit_and_push(self, service, ai_provider, worktree_port):
        ai_provider.generate_text = AsyncMock(return_value="fix: address review")
        worktree_port.has_changes.return_value = True
        worktree_port.commit_and_push.return_value = ""

        from unittest.mock import patch
        with patch("app.services.fix_service.os.path.isdir", return_value=True):
            result = await service.push_fix("acme/backend", 1, "diff", "fix this")
        assert result["status"] == "pushed"
        assert "commit_message" in result
        worktree_port.stage_all.assert_not_called()
        worktree_port.commit_and_push.assert_called_once()

    async def test_raises_worktree_not_found(self, service, worktree_port):
        from unittest.mock import patch
        from app.domain.exceptions import WorktreeNotFoundError
        with patch("app.services.fix_service.os.path.isdir", return_value=False):
            with pytest.raises(WorktreeNotFoundError):
                await service.push_fix("acme/backend", 1, "diff", "fix this")

    async def test_raises_no_changes(self, service, worktree_port):
        from unittest.mock import patch
        from app.domain.exceptions import WorktreeNoChangesError
        worktree_port.has_changes.return_value = False
        with patch("app.services.fix_service.os.path.isdir", return_value=True):
            with pytest.raises(WorktreeNoChangesError):
                await service.push_fix("acme/backend", 1, "diff", "fix this")


class TestCreatePrFromFix:
    async def test_creates_pr_successfully(self, service, ai_provider, worktree_port, vcs_port):
        ai_provider.generate_text = AsyncMock(return_value="TITLE: fix\nBODY:\nfixes review")
        worktree_port.create_branch_and_push.return_value = ""
        vcs_port.create_pr.return_value = {"url": "https://github.com/acme/backend/pull/2"}

        from unittest.mock import patch
        with patch("app.services.fix_service.os.path.isdir", return_value=True):
            result = await service.create_pr_from_fix("acme/backend", 1, "feature/fix", "diff", "fix this")

        assert result["status"] == "created"
        assert result["pr_url"] == "https://github.com/acme/backend/pull/2"
        assert result["branch"].startswith("fix/pr1-review-")

    async def test_falls_back_to_default_title_on_ai_failure(self, service, ai_provider, worktree_port, vcs_port):
        ai_provider.generate_text = AsyncMock(side_effect=Exception("timeout"))
        worktree_port.create_branch_and_push.return_value = ""
        vcs_port.create_pr.return_value = {"url": "https://github.com/acme/backend/pull/2"}

        from unittest.mock import patch
        with patch("app.services.fix_service.os.path.isdir", return_value=True):
            result = await service.create_pr_from_fix("acme/backend", 1, "feature/fix", "diff", "fix this")

        assert result["status"] == "created"
        assert "1" in result["pr_title"]  # default includes PR number
