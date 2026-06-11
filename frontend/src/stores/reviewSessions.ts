import { writable, derived, get } from 'svelte/store';
import type { PR, Repo, Review, SSEReviewEvent } from '../lib/types';
import { connectReviewStream, type SSECleanup } from '../lib/sse';
import { selectedModel, showToast } from './ui';

export type SessionStatus = 'connecting' | 'streaming' | 'done' | 'error';

export interface ActivityEntry {
  offset: string;
  kind: 'progress' | 'lifecycle' | 'warning' | 'meta';
  text: string;
}

/**
 * One background review run, keyed by PR. The SSE connection lives here — at
 * module level — so switching PRs, repos or views never interrupts a running
 * review. Components only render session state; they never own the stream.
 *
 * Sessions are in-memory only: a page reload closes the EventSources, which
 * cancels the backend runs (the SSE generator can't be re-joined).
 */
export interface ReviewSession {
  key: string;
  repo: Repo;
  pr: PR;
  status: SessionStatus;
  review: Review | null;
  chunkCount: number;
  totalBytes: number;
  chunkLog: string;
  /** Epoch ms of the first chunk/progress event; 0 while still connecting. */
  startTime: number;
  endTime: number | null;
  latestProgress: string;
  warnings: string[];
  activity: ActivityEntry[];
  requestId: string;
  errorMsg: string;
  cleanup: SSECleanup | null;
}

export const reviewSessions = writable<Map<string, ReviewSession>>(new Map());

/** Number of reviews currently connecting/streaming, across all repos. */
export const runningReviewCount = derived(reviewSessions, (m) => {
  let n = 0;
  for (const s of m.values()) {
    if (s.status === 'connecting' || s.status === 'streaming') n++;
  }
  return n;
});

export function prKey(owner: string, repo: string, prNumber: number): string {
  return `${owner}/${repo}#${prNumber}`;
}

const ACTIVITY_CAP = 300;

function appendActivity(
  s: ReviewSession,
  startTime: number,
  entries: { kind: ActivityEntry['kind']; text: string }[],
): ActivityEntry[] {
  const sec = startTime ? (Date.now() - startTime) / 1000 : 0;
  const offset = `${sec.toFixed(1)}s`;
  const next = [...s.activity, ...entries.map((e) => ({ offset, ...e }))];
  return next.length > ACTIVITY_CAP ? next.slice(next.length - ACTIVITY_CAP) : next;
}

function patchSession(key: string, patch: (s: ReviewSession) => Partial<ReviewSession>): void {
  reviewSessions.update((m) => {
    const s = m.get(key);
    if (!s) return m;
    return new Map(m).set(key, { ...s, ...patch(s) });
  });
}

function handleEvent(key: string, event: SSEReviewEvent): void {
  if (event.type === 'error') showToast(event.message, 'error');
  patchSession(key, (s) => {
    switch (event.type) {
      case 'meta':
        return {
          requestId: event.request_id,
          activity: event.request_id
            ? appendActivity(s, s.startTime, [{ kind: 'meta', text: `request id: ${event.request_id}` }])
            : s.activity,
        };
      case 'chunk':
        return {
          status: s.status === 'connecting' ? 'streaming' : s.status,
          startTime: s.startTime || Date.now(),
          chunkCount: s.chunkCount + 1,
          totalBytes: s.totalBytes + event.text.length,
          chunkLog: s.chunkLog + event.text,
        };
      case 'progress': {
        const startTime = s.startTime || Date.now();
        return {
          startTime,
          latestProgress: event.text,
          activity: appendActivity(s, startTime, [
            { kind: event.text.startsWith('[chunk ') ? 'lifecycle' : 'progress', text: event.text },
          ]),
        };
      }
      case 'warning':
        return {
          warnings: [...s.warnings, ...event.lines],
          activity: appendActivity(s, s.startTime, event.lines.map((text) => ({ kind: 'warning' as const, text }))),
        };
      case 'result':
        return { review: event.review, status: 'done', endTime: Date.now() };
      case 'done':
        // `done` follows both `result` and `error` — don't clobber those states.
        return s.status === 'done' || s.status === 'error'
          ? {}
          : { status: 'done', endTime: Date.now() };
      case 'error':
        return { status: 'error', errorMsg: event.message, endTime: Date.now() };
    }
  });
}

/**
 * Launch a review for a PR in the background. Replaces (and disconnects) any
 * existing session for the same PR. There is no concurrency cap: each call
 * opens its own SSE stream and the backend handles them independently.
 */
export function startReview(repo: Repo, pr: PR, rerun = false): void {
  const key = prKey(repo.owner, repo.name, pr.number);
  get(reviewSessions).get(key)?.cleanup?.();

  // Connect before inserting the session so it is never observable with a
  // null cleanup. EventSource delivers events asynchronously, so none can
  // arrive before the session exists.
  const cleanup = connectReviewStream(
    repo.owner,
    repo.name,
    pr.number,
    (event) => handleEvent(key, event),
    rerun,
    get(selectedModel) || undefined,
  );

  const session: ReviewSession = {
    key,
    repo,
    pr,
    status: 'connecting',
    review: null,
    chunkCount: 0,
    totalBytes: 0,
    chunkLog: '',
    startTime: 0,
    endTime: null,
    latestProgress: '',
    warnings: [],
    activity: [],
    requestId: '',
    errorMsg: '',
    cleanup,
  };
  reviewSessions.update((m) => new Map(m).set(key, session));
}

/**
 * Stop a running review. The backend run is cancelled (closing the SSE stream
 * aborts the generator) and the session is dropped, returning the PR to its
 * idle "Run Review" state. No-op for finished sessions.
 */
export function stopReview(key: string): void {
  const s = get(reviewSessions).get(key);
  if (!s) return;
  if (s.status !== 'connecting' && s.status !== 'streaming') return;
  s.cleanup?.();
  reviewSessions.update((m) => {
    const next = new Map(m);
    next.delete(key);
    return next;
  });
}
