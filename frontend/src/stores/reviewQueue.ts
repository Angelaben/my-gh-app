import { writable, derived, get } from 'svelte/store';
import type { PR, Repo } from '../lib/types';
import { reviewSessions, prKey, startReview, stopReview } from './reviewSessions';

interface QueueItem {
  repo: Repo;
  pr: PR;
  key: string;
}

interface QueueState {
  items: QueueItem[]; // pending, FIFO
  runningKeys: string[]; // keys we started and that are still in flight
  concurrency: number;
}

export const reviewQueue = writable<QueueState>({ items: [], runningKeys: [], concurrency: 2 });
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

export function enqueueReviews(repo: Repo, prsToQueue: PR[], concurrency = 2): void {
  if (prsToQueue.length === 0) return;
  reviewQueue.update((q) => {
    const known = new Set([...q.items.map((i) => i.key), ...q.runningKeys]);
    const fresh = prsToQueue
      .map((pr) => ({ repo, pr, key: prKey(repo.owner, repo.name, pr.number) }))
      .filter((i) => !known.has(i.key));
    return { ...q, items: [...q.items, ...fresh], concurrency: Math.max(1, concurrency) };
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
