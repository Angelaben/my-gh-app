"""VCS port — GitHub operations (PRs, diffs, comments)."""
from abc import ABC, abstractmethod

from app.domain.models import PR, Comment


class VCSPort(ABC):
    """Abstract VCS provider. Implement this to add a new VCS backend."""

    @abstractmethod
    def list_prs(self, repo_full_name: str) -> list[PR]:
        """List open pull requests for a repository."""
        ...

    @abstractmethod
    def get_diff(self, repo_full_name: str, pr_number: int) -> str:
        """Return the unified diff for a PR."""
        ...

    @abstractmethod
    def get_comments(self, repo_full_name: str, pr_number: int) -> dict:
        """Return comments, reviews, and inline review comments for a PR."""
        ...

    @abstractmethod
    def post_comment(self, repo_full_name: str, pr_number: int, body: str) -> str:
        """Post a top-level comment on a PR."""
        ...

    @abstractmethod
    def post_inline_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        path: str,
        line: int,
        commit_id: str | None = None,
    ) -> dict:
        """Post an inline review comment on a specific file/line."""
        ...

    @abstractmethod
    def get_pr_head_branch(self, repo_full_name: str, pr_number: int) -> str:
        """Return the head branch name for a PR."""
        ...

    @abstractmethod
    def search_repos(self, org: str, query: str = "") -> list[dict]:
        """Search repositories in an organization."""
        ...

    @abstractmethod
    def create_pr(
        self,
        repo_full_name: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> dict:
        """Create a PR and return {'url': str}."""
        ...

    @abstractmethod
    def get_authenticated_user(self) -> str:
        """Return the GitHub login of the currently authenticated user."""
        ...

    @abstractmethod
    def delete_comment(self, repo_full_name: str, comment_id: int, comment_type: str) -> None:
        """Delete a comment by ID. comment_type: 'pr_comment' | 'review_comment'."""
        ...
