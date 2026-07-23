import { writable, derived, get } from 'svelte/store';
import type { PR, Repo } from '../lib/types';
import { reviewSessions, prKey, startReview, stopReview } from './reviewSessions';
import { reviewSettings } from './settings';

interface QueueItem {
  repo: Repo;
  pr: PR;
  key: string;
}

interface QueueState {
  items: QueueItem[]; // pending, FIFO
  runningKeys: string[]; // keys we started and that are still in flight
  concurrency: number; // max PR reviews to run at once (from review_max_concurrency)
}

// Bootstrap cap used only until real settings load — matches the backend
// DEFAULT_MAX_CONCURRENCY so the first batch behaves sanely even offline.
const DEFAULT_CONCURRENCY = 3;

export const reviewQueue = writable<QueueState>({
  items: [],
  runningKeys: [],
  concurrency: DEFAULT_CONCURRENCY,
});
export const queueOpen = writable<boolean>(false);

/** Selection mode in the PR list + the set of selected PR numbers. */
export const selectionMode = writable<boolean>(false);
export const selectedPRNumbers = writable<Set<number>>(new Set());

export const queueActive = derived(
  reviewQueue,
  (q) => q.items.length > 0 || q.runningKeys.length > 0,
);

function isRunning(key: string): boolean {
  const s = get(reviewSessions).get(key);
  return !!s && (s.status === 'connecting' || s.status === 'streaming');
}

/** Start as many pending reviews as the concurrency cap allows. */
function pump(): void {
  reviewQueue.update((q) => {
    const runningKeys = q.runningKeys.filter(isRunning);
    const items = [...q.items];
    while (runningKeys.length < q.concurrency && items.length > 0) {
      const item = items.shift()!;
      startReview(item.repo, item.pr);
      runningKeys.push(item.key);
    }
    return { ...q, items, runningKeys };
  });
}

export function enqueueReviews(repo: Repo, prsToQueue: PR[]): void {
  if (prsToQueue.length === 0) return;
  reviewQueue.update((q) => {
    const known = new Set([...q.items.map((i) => i.key), ...q.runningKeys]);
    const fresh = prsToQueue
      .map((pr) => ({ repo, pr, key: prKey(repo.owner, repo.name, pr.number) }))
      .filter((i) => !known.has(i.key));
    return { ...q, items: [...q.items, ...fresh] };
  });
  queueOpen.set(true);
  pump();
}

/** Drop everything pending and stop the in-flight reviews this queue started. */
export function stopQueue(): void {
  const q = get(reviewQueue);
  for (const key of q.runningKeys) stopReview(key);
  reviewQueue.set({ items: [], runningKeys: [], concurrency: q.concurrency });
}

export function clearSelection(): void {
  selectedPRNumbers.set(new Set());
}

export function toggleSelected(prNumber: number): void {
  selectedPRNumbers.update((s) => {
    const next = new Set(s);
    if (next.has(prNumber)) next.delete(prNumber);
    else next.add(prNumber);
    return next;
  });
}

// Whenever any review session changes state, advance the queue (a finished
// review frees a slot). pump() is a no-op when nothing is pending.
reviewSessions.subscribe(() => {
  const q = get(reviewQueue);
  if (q.items.length > 0 || q.runningKeys.length > 0) pump();
});

// The configured "max concurrent reviews" cap owns the queue's concurrency.
// It's loaded at app startup and refreshed on every settings save, so the
// queue always reflects the real value — no stale per-call override. Raising
// the cap immediately pumps any pending reviews into the freed slots.
reviewSettings.subscribe((s) => {
  if (!s) return;
  reviewQueue.update((q) => ({ ...q, concurrency: Math.max(1, s.review_max_concurrency) }));
  pump();
});
