"""LiveReviewService — opt-in background watcher that auto-reviews PRs.

While started, a single asyncio loop polls every registered repo on a
configurable interval (``live_review_poll_interval``, seconds). For each
open, non-draft PR it decides whether to trigger a review:

- **No cached review** → the PR is new to the tool → review it.
- **Cached review with a head SHA** → re-review only when the PR's current
  head SHA differs from the reviewed one. ``updated_at`` alone is NOT a
  trigger: GitHub bumps it on every comment — including the review comments
  this tool publishes — which would loop the watcher on its own output.
  ``updated_at`` vs the review's ``created_at`` is used only as a cheap
  pre-filter to skip the per-PR head-SHA lookup.
- **Cached review without a head SHA** → skipped (cannot tell code changes
  apart from comment activity).

Triggered reviews run as detached asyncio tasks through
``ReviewService.rerun_review``, which persists the result in the normal
review cache — so it shows up in the PR's Review tab exactly like a manual
run. ``stop()`` cancels only the polling loop; in-flight reviews finish.

Every state change is appended to an in-memory event ring buffer (same
pattern as :mod:`app.log_buffer`) served by ``/api/live-review/events`` and
streamed over SSE for the Live Review tab's activity feed.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.domain.models import PR, Review
from app.ports.cache_port import CachePort
from app.ports.vcs_port import VCSPort
from app.services import settings_store
from app.services.review_service import ReviewService

logger = logging.getLogger(__name__)

_EVENT_CAPACITY = 500
# Auto-triggered reviews running at once. Each spawns one AI CLI subprocess;
# keep this conservative — a busy org can surface many PRs in a single poll.
_MAX_CONCURRENT_REVIEWS = 2


def _parse_ts(value: str | None) -> datetime | None:
    """Parse GitHub / isoformat timestamps; None when absent or malformed."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class LiveReviewService:
    """Polls registered repos and auto-triggers reviews on new/updated PRs."""

    def __init__(self, review_service: ReviewService, cache: CachePort, vcs: VCSPort) -> None:
        self._review_service = review_service
        self._cache = cache
        self._vcs = vcs

        self._poll_task: asyncio.Task | None = None
        self._review_tasks: dict[str, asyncio.Task] = {}  # "owner/repo#N" -> task
        self._review_sem = asyncio.Semaphore(_MAX_CONCURRENT_REVIEWS)
        self._last_poll_at: str | None = None

        self._events: list[dict] = []
        self._next_seq = 1

    # --- lifecycle -----------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._poll_task is not None and not self._poll_task.done()

    def start(self) -> dict:
        """Start the polling loop (no-op when already running)."""
        if not self.running:
            self._poll_task = asyncio.create_task(self._poll_loop())
            self._emit(
                "started",
                f"Live review started — polling every {self._poll_interval()}s",
            )
            logger.info("live-review | started | interval=%ds", self._poll_interval())
        return self.status()

    def stop(self) -> dict:
        """Stop polling. In-flight reviews are NOT cancelled — they finish
        and land in the review cache like any manual run."""
        if self.running:
            assert self._poll_task is not None
            self._poll_task.cancel()
            self._poll_task = None
            in_flight = len(self._review_tasks)
            suffix = f" — {in_flight} in-flight review(s) will finish" if in_flight else ""
            self._emit("stopped", f"Live review stopped{suffix}")
            logger.info("live-review | stopped | in_flight=%d", in_flight)
        return self.status()

    def status(self) -> dict:
        return {
            "running": self.running,
            "poll_interval": self._poll_interval(),
            "last_poll_at": self._last_poll_at,
            "active_reviews": sorted(self._review_tasks),
            "last_seq": self._next_seq - 1,
        }

    # --- events --------------------------------------------------------------

    def _emit(self, kind: str, message: str, *, repo: str | None = None,
              pr: int | None = None) -> None:
        event: dict = {
            "seq": self._next_seq,
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "message": message,
        }
        if repo is not None:
            event["repo"] = repo
        if pr is not None:
            event["pr"] = pr
        self._next_seq += 1
        self._events.append(event)
        if len(self._events) > _EVENT_CAPACITY:
            del self._events[: len(self._events) - _EVENT_CAPACITY]

    def events_after(self, after_seq: int = 0, limit: int = 200) -> list[dict]:
        """Events with seq > after_seq, oldest first, capped to the last `limit`."""
        items = [e for e in self._events if e["seq"] > after_seq]
        if limit and len(items) > limit:
            items = items[-limit:]
        return items

    @property
    def last_seq(self) -> int:
        return self._next_seq - 1

    # --- polling -------------------------------------------------------------

    def _poll_interval(self) -> int:
        return settings_store.get_review_settings()["live_review_poll_interval"]

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — the loop must survive a bad cycle
                logger.exception("live-review | poll cycle failed")
                self._emit("error", f"Poll cycle failed: {type(exc).__name__}: {exc}")
            # Re-read the interval each cycle so a settings change applies
            # without restarting the watcher.
            await asyncio.sleep(self._poll_interval())

    async def poll_once(self) -> None:
        """One poll cycle over all registered repos."""
        repos = self._cache.get_repos()
        self._last_poll_at = datetime.now(timezone.utc).isoformat()
        triggered = 0
        for repo in repos:
            full_name = repo["full_name"]
            try:
                prs = await asyncio.to_thread(self._vcs.list_prs, full_name)
            except Exception as exc:  # noqa: BLE001 — one bad repo must not kill the cycle
                logger.warning("live-review | list_prs failed | repo=%s | %s", full_name, exc)
                self._emit(
                    "error",
                    f"Failed to list PRs: {type(exc).__name__}: {exc}",
                    repo=full_name,
                )
                continue
            for pr in prs:
                if pr.is_draft:
                    continue
                reason = await self._trigger_reason(full_name, pr)
                if reason is None:
                    continue
                if self._launch_review(full_name, pr, reason):
                    triggered += 1
        self._emit(
            "poll",
            f"Polled {len(repos)} repo(s) — {triggered} review(s) triggered",
        )

    async def _trigger_reason(self, repo_full_name: str, pr: PR) -> str | None:
        """Why this PR needs a review, or None to skip it."""
        try:
            cached: Review | None = self._cache.get_review(repo_full_name, pr.number)
        except Exception as exc:  # noqa: BLE001 — unreadable cache must not stop the watcher
            logger.warning(
                "live-review | review cache read failed | repo=%s pr=#%d | %s",
                repo_full_name, pr.number, exc,
            )
            return None
        if cached is None:
            return "new PR (no review yet)"
        if not cached.head_sha:
            # Without a reviewed SHA we can't distinguish new commits from
            # comment activity (including our own published comments).
            return None

        # Cheap pre-filter: untouched since the review → skip the gh call.
        updated = _parse_ts(pr.updated_at)
        reviewed = _parse_ts(cached.created_at)
        if updated is not None and reviewed is not None and updated <= reviewed:
            return None

        try:
            current_sha = await asyncio.to_thread(
                self._vcs.get_pr_head_sha, repo_full_name, pr.number
            )
        except Exception as exc:  # noqa: BLE001 — advisory lookup only
            logger.warning(
                "live-review | head sha lookup failed | repo=%s pr=#%d | %s",
                repo_full_name, pr.number, exc,
            )
            return None
        if current_sha and current_sha != cached.head_sha:
            return f"new commits since review ({cached.head_sha[:7]} → {current_sha[:7]})"
        return None

    # --- review runs ---------------------------------------------------------

    def _launch_review(self, repo_full_name: str, pr: PR, reason: str) -> bool:
        """Start a detached review task for the PR; False when already in flight."""
        key = f"{repo_full_name}#{pr.number}"
        if key in self._review_tasks:
            return False
        self._emit(
            "review_triggered",
            f"PR #{pr.number} “{pr.title}” — {reason} — review queued",
            repo=repo_full_name,
            pr=pr.number,
        )
        task = asyncio.create_task(self._run_review(repo_full_name, pr))
        self._review_tasks[key] = task
        task.add_done_callback(lambda _t, k=key: self._review_tasks.pop(k, None))
        return True

    async def _run_review(self, repo_full_name: str, pr: PR) -> None:
        async with self._review_sem:
            self._emit(
                "review_started",
                f"Review of PR #{pr.number} started",
                repo=repo_full_name,
                pr=pr.number,
            )
            try:
                review = await self._review_service.rerun_review(repo_full_name, pr.number)
            except Exception as exc:  # noqa: BLE001 — surface the failure in the feed
                logger.exception(
                    "live-review | review failed | repo=%s pr=#%d", repo_full_name, pr.number
                )
                self._emit(
                    "review_failed",
                    f"Review of PR #{pr.number} failed: {type(exc).__name__}: {exc}",
                    repo=repo_full_name,
                    pr=pr.number,
                )
                return
            self._emit(
                "review_finished",
                f"Review of PR #{pr.number} finished — "
                f"{len(review.findings)} finding(s) — available in the PR's Review tab",
                repo=repo_full_name,
                pr=pr.number,
            )
