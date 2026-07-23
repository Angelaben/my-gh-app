import { writable, get } from 'svelte/store';
import type { LiveReviewEvent, LiveReviewStatus, SSELiveReviewEvent } from '../lib/types';
import { connectSSE, type SSECleanup } from '../lib/sse';
import { api } from '../lib/api';
import { showToast } from './ui';

/**
 * Live Review state. The SSE connection lives at module level (like review
 * sessions) so switching tabs never drops the feed, and toasts for
 * auto-triggered reviews fire even while the user is on another view.
 * "Not listening when off" refers to GitHub polling — that lives entirely
 * in the backend loop; this SSE stream only watches our own server.
 */
export const liveReviewStatus = writable<LiveReviewStatus | null>(null);
export const liveReviewEvents = writable<LiveReviewEvent[]>([]);

const EVENT_CAP = 500;

let cleanup: SSECleanup | null = null;

function appendEvents(events: LiveReviewEvent[]): void {
  if (events.length === 0) return;
  liveReviewEvents.update((current) => {
    const seen = new Set(current.map((e) => e.seq));
    const fresh = events.filter((e) => !seen.has(e.seq));
    if (fresh.length === 0) return current;
    const next = [...current, ...fresh];
    return next.length > EVENT_CAP ? next.slice(next.length - EVENT_CAP) : next;
  });
}

function notify(event: LiveReviewEvent): void {
  if (event.kind === 'review_triggered') showToast(event.message, 'info');
  else if (event.kind === 'review_finished') showToast(event.message, 'success');
  else if (event.kind === 'review_failed') showToast(event.message, 'error');
}

/** Connect the feed (idempotent) and backfill history + current status. */
export async function initLiveReview(): Promise<void> {
  if (cleanup === null) {
    cleanup = connectSSE<SSELiveReviewEvent>('/api/live-review/stream', (ev) => {
      if (ev.type === 'status') {
        liveReviewStatus.set(ev.status);
      } else if (ev.type === 'event') {
        // Skip toasts for backfilled events: only notify for events newer
        // than what we already had.
        const known = get(liveReviewEvents);
        const isNew = known.length === 0 || ev.event.seq > known[known.length - 1].seq;
        appendEvents([ev.event]);
        if (isNew) notify(ev.event);
      }
    });
  }
  try {
    const data = await api.get<{
      events: LiveReviewEvent[];
      last_seq: number;
      status: LiveReviewStatus;
    }>('/live-review/events?limit=200');
    appendEvents(data.events);
    liveReviewStatus.set(data.status);
  } catch {
    /* non-fatal — the stream will populate status */
  }
}

export async function startLiveReview(): Promise<void> {
  liveReviewStatus.set(await api.post<LiveReviewStatus>('/live-review/start'));
}

export async function stopLiveReview(): Promise<void> {
  liveReviewStatus.set(await api.post<LiveReviewStatus>('/live-review/stop'));
}
