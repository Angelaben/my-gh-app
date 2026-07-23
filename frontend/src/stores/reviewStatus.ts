import { writable } from 'svelte/store';
import { api } from '../lib/api';
import { reviewSettings } from './settings';
import { liveReviewEvents } from './liveReview';
import type { LiveReviewEvent } from '../lib/types';

/**
 * Per-repo "needs review" counts backing the sidebar pastille. Sourced from
 * GET /api/review/pending (one `gh pr list` per repo, cheap timestamp-only
 * estimate — see ReviewService.needs_review_estimate). Refreshed on the same
 * configurable cadence as the open-PR list, plus promptly whenever the Live
 * Review watcher triggers or finishes a review so the dot appears/clears.
 */
export const pendingByRepo = writable<Record<string, number>>({});

const DEFAULT_INTERVAL_S = 900;

export async function loadPendingReview(): Promise<void> {
  try {
    const data = await api.get<{ by_repo: Record<string, number>; total: number }>(
      '/review/pending',
    );
    pendingByRepo.set(data.by_repo ?? {});
  } catch {
    /* non-fatal — the next tick retries */
  }
}

let started = false;
let timer: ReturnType<typeof setInterval> | null = null;
let currentInterval = 0;
let lastSeenSeq = 0;

/** Rebuild the polling timer to match the current refresh interval. */
function ensureTimer(seconds: number): void {
  if (timer !== null && seconds === currentInterval) return;
  if (timer !== null) clearInterval(timer);
  currentInterval = seconds;
  timer = setInterval(() => void loadPendingReview(), seconds * 1000);
}

/** Idempotent: start polling + wire refresh triggers. Call once at app mount. */
export function initReviewStatus(): void {
  if (started) return;
  started = true;

  void loadPendingReview();

  // (Re)build the timer whenever the configured interval changes.
  reviewSettings.subscribe((s) => {
    ensureTimer(s?.pr_list_refresh_interval ?? DEFAULT_INTERVAL_S);
  });

  // Refresh promptly when the watcher triggers or finishes a review. Only look
  // at events newer than the last one we processed to avoid re-firing on every
  // store update (backfills, status frames, etc.).
  liveReviewEvents.subscribe((events: LiveReviewEvent[]) => {
    if (events.length === 0) return;
    const fresh = events.filter((e) => e.seq > lastSeenSeq);
    if (fresh.length === 0) return;
    lastSeenSeq = fresh[fresh.length - 1].seq;
    if (fresh.some((e) => e.kind === 'review_triggered' || e.kind === 'review_finished')) {
      void loadPendingReview();
    }
  });
}
