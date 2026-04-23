"""Tests for domain models and exceptions."""
import pytest
from app.domain.models import Finding, Review, Repo, PR, Comment, FixResult
from app.domain.exceptions import ProviderError, VCSError, CacheError, WorktreeError


class TestFinding:
    def test_finding_creation(self):
        f = Finding(
            priority="P1",
            title="Null check missing",
            description="Field may be None",
            file="app/main.py",
            line=42,
            suggestion="Add None guard",
        )
        assert f.priority == "P1"
        assert f.file == "app/main.py"

    def test_finding_optional_fields(self):
        f = Finding(priority="P2", title="Style issue", description="Use f-string")
        assert f.file is None
        assert f.line is None
        assert f.suggestion is None


class TestReview:
    def test_review_creation(self):
        r = Review(summary="LGTM", findings=[])
        assert r.summary == "LGTM"
        assert r.findings == []

    def test_review_with_findings(self):
        f = Finding(priority="P0", title="SQL injection", description="Unescaped input")
        r = Review(summary="Critical issues found", findings=[f])
        assert len(r.findings) == 1
        assert r.findings[0].priority == "P0"

    def test_review_raw_output_optional(self):
        r = Review(summary="Unparseable", findings=[], raw_output="raw text", raw_length=8)
        assert r.raw_output == "raw text"


class TestRepo:
    def test_repo_full_name(self):
        repo = Repo(owner="acme", name="backend")
        assert repo.full_name == "acme/backend"


class TestComment:
    def test_comment_defaults(self):
        c = Comment(id=1, author="alice", body="LGTM")
        assert c.file is None
        assert c.line is None
        assert c.created_at is None


class TestFixResult:
    def test_fix_result(self):
        fr = FixResult(
            worktree_path="/tmp/wt",
            branch="feature/fix",
            has_changes=True,
            diff="--- a/foo.py\n+++ b/foo.py",
            output="Done",
        )
        assert fr.has_changes is True


class TestExceptions:
    def test_provider_error(self):
        e = ProviderError("opencode timed out")
        assert "opencode timed out" in str(e)

    def test_vcs_error(self):
        e = VCSError("gh command failed")
        assert isinstance(e, VCSError)

    def test_cache_error(self):
        e = CacheError("JSON decode error")
        assert isinstance(e, CacheError)

    def test_worktree_error(self):
        e = WorktreeError("branch not found")
        assert isinstance(e, WorktreeError)
