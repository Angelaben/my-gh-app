"""ReviewService — orchestrates AI, VCS, and cache ports for PR review."""
from collections.abc import AsyncGenerator

from app.domain.models import Review
from app.ports.ai_provider import AIProvider, ReviewResultEvent, ReviewStreamEvent
from app.ports.cache_port import CachePort
from app.ports.vcs_port import VCSPort


class ReviewService:
    def __init__(self, ai: AIProvider, cache: CachePort, vcs: VCSPort) -> None:
        self._ai = ai
        self._cache = cache
        self._vcs = vcs

    async def get_or_run_review(self, repo_full_name: str, pr_number: int) -> Review:
        """Return cached review or run a fresh one."""
        cached = self._cache.get_review(repo_full_name, pr_number)
        if cached is not None:
            return cached
        return await self._run_review(repo_full_name, pr_number)

    async def rerun_review(self, repo_full_name: str, pr_number: int) -> Review:
        """Always run a fresh review, overwriting any cache."""
        return await self._run_review(repo_full_name, pr_number)

    async def stream_review(
        self, repo_full_name: str, pr_number: int, model: str | None = None
    ) -> AsyncGenerator[ReviewStreamEvent, None]:
        """Stream review events. Saves the Review to cache when the result event arrives."""
        diff = self._vcs.get_diff(repo_full_name, pr_number)
        async for event in self._ai.stream_review(repo_full_name, pr_number, diff, model=model):
            if isinstance(event, ReviewResultEvent):
                self._cache.save_review(repo_full_name, pr_number, event.review)
            yield event

    async def _run_review(self, repo_full_name: str, pr_number: int) -> Review:
        diff = self._vcs.get_diff(repo_full_name, pr_number)
        review: Review | None = None
        async for event in self._ai.stream_review(repo_full_name, pr_number, diff):
            if isinstance(event, ReviewResultEvent):
                review = event.review
        if review is None:
            review = Review(summary="No review result received.", findings=[])
        self._cache.save_review(repo_full_name, pr_number, review)
        return review

    def clear_cache(self, repo_full_name: str, pr_number: int) -> None:
        self._cache.clear_review(repo_full_name, pr_number)
