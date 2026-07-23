"""Tests for LiveReviewService (background PR watcher)."""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.domain.models import PR, Review
from app.services.live_review_service import LiveReviewService


def _make_pr(number: int = 1, *, is_draft: bool = False,
             updated_at: str = "2026-07-03T12:00:00Z") -> PR:
    return PR(
        number=number,
        title=f"PR {number}",
        author="octocat",
        branch=f"feature-{number}",
        base_branch="master",
        additions=1,
        deletions=1,
        updated_at=updated_at,
        url=f"https://github.com/acme/backend/pull/{number}",
        is_draft=is_draft,
    )


def _make_review(head_sha: str | None = "abc1234def",
                 created_at: str | None = "2026-07-03T10:00:00+00:00") -> Review:
    return Review(summary="ok", findings=[], head_sha=head_sha, created_at=created_at)


@pytest.fixture
def review_service():
    svc = MagicMock()
    svc.rerun_review = AsyncMock(return_value=Review(summary="fresh", findings=[]))
    return svc


@pytest.fixture
def cache_port():
    cache = MagicMock()
    cache.get_repos.return_value = [
        {"owner": "acme", "name": "backend", "full_name": "acme/backend"},
    ]
    cache.get_review.return_value = None
    return cache


@pytest.fixture
def vcs_port():
    vcs = MagicMock()
    vcs.list_prs.return_value = []
    vcs.get_pr_head_sha.return_value = "abc1234def"
    return vcs


@pytest.fixture
def service(review_service, cache_port, vcs_port):
    return LiveReviewService(review_service=review_service, cache=cache_port, vcs=vcs_port)


async def _settle(service: LiveReviewService) -> None:
    """Wait for all in-flight review tasks to finish."""
    while service._review_tasks:
        await asyncio.gather(*service._review_tasks.values(), return_exceptions=True)


class TestTriggerRules:
    async def test_new_pr_without_review_triggers(self, service, review_service, vcs_port):
        vcs_port.list_prs.return_value = [_make_pr(7)]
        await service.poll_once()
        await _settle(service)
        review_service.rerun_review.assert_awaited_once_with("acme/backend", 7)

    async def test_draft_pr_is_skipped(self, service, review_service, vcs_port):
        vcs_port.list_prs.return_value = [_make_pr(7, is_draft=True)]
        await service.poll_once()
        await _settle(service)
        review_service.rerun_review.assert_not_awaited()

    async def test_unchanged_head_sha_does_not_trigger(
        self, service, review_service, cache_port, vcs_port
    ):
        cache_port.get_review.return_value = _make_review(head_sha="abc1234def")
        vcs_port.get_pr_head_sha.return_value = "abc1234def"
        vcs_port.list_prs.return_value = [_make_pr(7)]
        await service.poll_once()
        await _settle(service)
        review_service.rerun_review.assert_not_awaited()

    async def test_new_head_sha_triggers_rereview(
        self, service, review_service, cache_port, vcs_port
    ):
        cache_port.get_review.return_value = _make_review(head_sha="abc1234def")
        vcs_port.get_pr_head_sha.return_value = "fff9999aaa"
        vcs_port.list_prs.return_value = [_make_pr(7)]
        await service.poll_once()
        await _settle(service)
        review_service.rerun_review.assert_awaited_once_with("acme/backend", 7)

    async def test_untouched_since_review_skips_sha_lookup(
        self, service, review_service, cache_port, vcs_port
    ):
        # updated_at older than the review's created_at → cheap skip, no gh call.
        cache_port.get_review.return_value = _make_review(
            created_at="2026-07-03T10:00:00+00:00"
        )
        vcs_port.list_prs.return_value = [_make_pr(7, updated_at="2026-07-03T09:00:00Z")]
        await service.poll_once()
        await _settle(service)
        vcs_port.get_pr_head_sha.assert_not_called()
        review_service.rerun_review.assert_not_awaited()

    async def test_review_without_head_sha_never_retriggers(
        self, service, review_service, cache_port, vcs_port
    ):
        # No reviewed SHA → comment activity (incl. our own published review
        # comments) is indistinguishable from code changes; must not loop.
        cache_port.get_review.return_value = _make_review(head_sha=None)
        vcs_port.list_prs.return_value = [_make_pr(7)]
        await service.poll_once()
        await _settle(service)
        review_service.rerun_review.assert_not_awaited()

    async def test_pr_already_in_flight_is_not_retriggered(
        self, service, review_service, vcs_port
    ):
        release = asyncio.Event()

        async def slow_review(repo, pr_number):
            await release.wait()
            return Review(summary="slow", findings=[])

        review_service.rerun_review = AsyncMock(side_effect=slow_review)
        vcs_port.list_prs.return_value = [_make_pr(7)]
        await service.poll_once()
        await service.poll_once()  # same PR still reviewing → no second task
        assert review_service.rerun_review.await_count == 1
        release.set()
        await _settle(service)

    async def test_failing_repo_does_not_break_the_cycle(
        self, service, review_service, cache_port, vcs_port
    ):
        cache_port.get_repos.return_value = [
            {"full_name": "acme/broken"},
            {"full_name": "acme/backend"},
        ]

        def list_prs(full_name):
            if full_name == "acme/broken":
                raise RuntimeError("gh exploded")
            return [_make_pr(7)]

        vcs_port.list_prs.side_effect = list_prs
        await service.poll_once()
        await _settle(service)
        review_service.rerun_review.assert_awaited_once_with("acme/backend", 7)
        assert any(e["kind"] == "error" for e in service.events_after())


class TestLifecycle:
    async def test_start_stop_toggle_running(self, service):
        assert service.running is False
        status = service.start()
        assert status["running"] is True
        assert service.start()["running"] is True  # idempotent
        status = service.stop()
        assert status["running"] is False
        kinds = [e["kind"] for e in service.events_after()]
        assert kinds.count("started") == 1
        assert kinds.count("stopped") == 1

    async def test_stop_does_not_cancel_in_flight_review(
        self, service, review_service, vcs_port
    ):
        release = asyncio.Event()

        async def slow_review(repo, pr_number):
            await release.wait()
            return Review(summary="survived", findings=[])

        review_service.rerun_review = AsyncMock(side_effect=slow_review)
        vcs_port.list_prs.return_value = [_make_pr(7)]

        service.start()
        await service.poll_once()
        assert len(service.status()["active_reviews"]) == 1
        service.stop()

        release.set()
        await _settle(service)
        kinds = [e["kind"] for e in service.events_after()]
        assert "review_finished" in kinds

    async def test_review_failure_is_reported_in_feed(
        self, service, review_service, vcs_port
    ):
        review_service.rerun_review = AsyncMock(side_effect=RuntimeError("provider died"))
        vcs_port.list_prs.return_value = [_make_pr(7)]
        await service.poll_once()
        await _settle(service)
        failed = [e for e in service.events_after() if e["kind"] == "review_failed"]
        assert len(failed) == 1
        assert "provider died" in failed[0]["message"]


class TestEventBuffer:
    async def test_events_after_filters_by_seq(self, service, vcs_port):
        vcs_port.list_prs.return_value = []
        await service.poll_once()
        await service.poll_once()
        events = service.events_after()
        assert [e["seq"] for e in events] == [1, 2]
        assert service.events_after(after_seq=1) == events[1:]
        assert service.last_seq == 2

    async def test_events_carry_repo_and_pr(self, service, vcs_port):
        vcs_port.list_prs.return_value = [_make_pr(42)]
        await service.poll_once()
        await _settle(service)
        triggered = [e for e in service.events_after() if e["kind"] == "review_triggered"]
        assert triggered[0]["repo"] == "acme/backend"
        assert triggered[0]["pr"] == 42
