"""AI provider port — swap opencode / Claude Code / Bedrock behind this interface."""
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import TypeAlias

from app.domain.models import Comment, Review


class ReviewChunkEvent:
    """Raw text chunk streamed from the AI provider during review."""
    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


class ReviewResultEvent:
    """Structured review result emitted once the provider finishes."""
    __slots__ = ("review",)

    def __init__(self, review: Review) -> None:
        self.review = review


class ReviewWarningEvent:
    """Stderr / diagnostic lines from the AI provider process."""
    __slots__ = ("lines",)

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines


ReviewStreamEvent: TypeAlias = ReviewChunkEvent | ReviewResultEvent | ReviewWarningEvent


class FixChunkEvent:
    """Raw text chunk streamed from the AI provider during fix generation."""
    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


class AIProvider(ABC):
    """Abstract AI provider. Implement this to add a new provider."""

    @abstractmethod
    async def stream_review(
        self, repo_full_name: str, pr_number: int, diff: str, model: str | None = None
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        """Stream review output. Must yield ReviewChunkEvent(s) then a final ReviewResultEvent."""
        yield

    @abstractmethod
    async def analyze_comments(
        self, repo_full_name: str, pr_number: int, comments: list[Comment]
    ) -> list[dict]:
        """Analyze comments and return analysis dicts."""
        ...

    @abstractmethod
    async def stream_fix(
        self, repo_dir: str, repo_full_name: str, pr_number: int, comment_body: str
    ) -> AsyncGenerator[FixChunkEvent, None]:
        """Stream fix implementation output."""
        yield

    @abstractmethod
    async def generate_text(self, prompt: str, timeout: int = 60) -> str:
        """Generate free-form text (used for commit messages, PR descriptions)."""
        ...
