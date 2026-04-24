"""Cache port — persisting repos, PRs, and reviews."""
from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.models import Review


class CachePort(ABC):
    """Abstract cache. Implement this to add a new storage backend."""

    @abstractmethod
    def get_repos(self) -> list[dict]:
        """Return all tracked repos."""
        ...

    @abstractmethod
    def add_repo(self, owner: str, name: str) -> list[dict]:
        """Add a repo and return the updated list."""
        ...

    @abstractmethod
    def remove_repo(self, full_name: str) -> list[dict]:
        """Remove a repo and return the updated list."""
        ...

    @abstractmethod
    def get_prs(self, repo_full_name: str) -> list[dict]:
        """Return cached PRs for a repo."""
        ...

    @abstractmethod
    def save_prs(self, repo_full_name: str, prs: list[dict]) -> None:
        """Persist PRs for a repo."""
        ...

    @abstractmethod
    def get_review(self, repo_full_name: str, pr_number: int) -> Review | None:
        """Return cached review for a PR, or None."""
        ...

    @abstractmethod
    def save_review(self, repo_full_name: str, pr_number: int, review: Review) -> None:
        """Persist a review for a PR."""
        ...

    @abstractmethod
    def clear_review(self, repo_full_name: str, pr_number: int) -> None:
        """Remove cached review for a PR."""
        ...

    @abstractmethod
    def get_last_visited(self, repo_full_name: str, pr_number: int) -> datetime | None:
        """Return the datetime the PR was last loaded, or None if never visited."""
        ...

    @abstractmethod
    def set_last_visited(self, repo_full_name: str, pr_number: int, dt: datetime) -> None:
        """Persist the datetime the PR was loaded."""
        ...

    @abstractmethod
    def get_github_login(self) -> str | None:
        """Return the cached authenticated GitHub login, or None."""
        ...

    @abstractmethod
    def set_github_login(self, login: str) -> None:
        """Persist the authenticated GitHub login."""
        ...
