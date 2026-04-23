"""Worktree port — local git checkout management."""
from abc import ABC, abstractmethod


class WorktreePort(ABC):
    """Abstract worktree manager."""

    @abstractmethod
    def create(self, repo_full_name: str, pr_number: int, pr_branch: str) -> str:
        """Create (or reset) a worktree for the PR branch. Returns the path."""
        ...

    @abstractmethod
    def remove(self, repo_full_name: str, pr_number: int) -> None:
        """Remove a worktree and prune stale references."""
        ...

    @abstractmethod
    def list_worktrees(self) -> list[dict]:
        """List all existing worktrees as {name, path} dicts."""
        ...

    @abstractmethod
    def has_changes(self, worktree_path: str) -> bool:
        """Return True if the worktree has uncommitted changes."""
        ...

    @abstractmethod
    def stage_all(self, worktree_path: str) -> None:
        """Stage all changes (git add -A) without side effects in unrelated calls."""
        ...

    @abstractmethod
    def get_staged_diff(self, worktree_path: str) -> str:
        """Return the diff of staged changes (must call stage_all first)."""
        ...

    @abstractmethod
    def commit_and_push(self, worktree_path: str, message: str) -> str:
        """Commit staged changes and push to origin. Returns push output."""
        ...

    @abstractmethod
    def create_branch_and_push(
        self, worktree_path: str, new_branch: str, message: str
    ) -> str:
        """Create a new branch, commit all changes, and push."""
        ...

    @abstractmethod
    def worktree_path(self, repo_full_name: str, pr_number: int) -> str:
        """Return the expected filesystem path for a worktree (may not exist yet)."""
        ...
