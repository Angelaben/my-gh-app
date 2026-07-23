import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import type { PR, Repo } from '../lib/types';

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  close() {
    this.closed = true;
  }
  emit(event: unknown) {
    this.onmessage?.({ data: JSON.stringify(event) });
  }
}
vi.stubGlobal('EventSource', MockEventSource);

vi.mock('../lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));
import { api } from '../lib/api';

import {
  reviewSessions,
  runningReviewCount,
  startReview,
  stopReview,
  prKey,
  hydrateReview,
  hydrateRepoReviews,
} from './reviewSessions';

const repo: Repo = { owner: 'acme', name: 'widgets', full_name: 'acme/widgets' };

function makePR(number: number): PR {
  return {
    number,
    title: `PR ${number}`,
    author: 'dev',
    branch: `feat/${number}`,
    base_branch: 'main',
    additions: 1,
    deletions: 1,
    updated_at: '2026-01-01T00:00:00Z',
    url: `https://github.com/acme/widgets/pull/${number}`,
    is_draft: false,
  };
}

function lastSource(): MockEventSource {
  return MockEventSource.instances[MockEventSource.instances.length - 1];
}

beforeEach(() => {
  reviewSessions.set(new Map());
  MockEventSource.instances = [];
  vi.mocked(api.get).mockReset();
});

describe('reviewSessions', () => {
  it('creates a connecting session keyed by owner/repo#number', () => {
    const pr = makePR(7);
    startReview(repo, pr);
    const session = get(reviewSessions).get(prKey('acme', 'widgets', 7));
    expect(session).toBeDefined();
    expect(session!.status).toBe('connecting');
    expect(session!.review).toBeNull();
    expect(get(runningReviewCount)).toBe(1);
  });

  it('transitions to streaming and accumulates chunks', () => {
    startReview(repo, makePR(7));
    const source = lastSource();
    source.emit({ type: 'chunk', text: 'hello ' });
    source.emit({ type: 'chunk', text: 'world' });
    const session = get(reviewSessions).get(prKey('acme', 'widgets', 7))!;
    expect(session.status).toBe('streaming');
    expect(session.chunkCount).toBe(2);
    expect(session.chunkLog).toBe('hello world');
    expect(session.startTime).toBeGreaterThan(0);
  });

  it('stores the review on result and finishes on done', () => {
    startReview(repo, makePR(7));
    const source = lastSource();
    const review = { findings: [{ priority: 'P1', title: 'bug', description: 'd' }] };
    source.emit({ type: 'result', review });
    source.emit({ type: 'done' });
    const session = get(reviewSessions).get(prKey('acme', 'widgets', 7))!;
    expect(session.status).toBe('done');
    expect(session.review).toMatchObject(review);
    expect(get(runningReviewCount)).toBe(0);
  });

  it('records errors without letting the trailing done event clobber them', () => {
    startReview(repo, makePR(7));
    const source = lastSource();
    source.emit({ type: 'error', message: 'boom' });
    source.emit({ type: 'done' });
    const session = get(reviewSessions).get(prKey('acme', 'widgets', 7))!;
    expect(session.status).toBe('error');
    expect(session.errorMsg).toBe('boom');
  });

  it('tracks independent sessions for different PRs', () => {
    startReview(repo, makePR(1));
    const first = lastSource();
    startReview(repo, makePR(2));
    const second = lastSource();

    first.emit({ type: 'result', review: { findings: [] } });
    first.emit({ type: 'done' });
    second.emit({ type: 'chunk', text: 'still going' });

    const map = get(reviewSessions);
    expect(map.get(prKey('acme', 'widgets', 1))!.status).toBe('done');
    expect(map.get(prKey('acme', 'widgets', 2))!.status).toBe('streaming');
    expect(get(runningReviewCount)).toBe(1);
  });

  it('replaces the session and closes the old stream on re-run', () => {
    startReview(repo, makePR(7));
    const first = lastSource();
    first.emit({ type: 'chunk', text: 'partial' });
    startReview(repo, makePR(7), true);
    const second = lastSource();

    expect(first.closed).toBe(true);
    expect(second.closed).toBe(false);
    const session = get(reviewSessions).get(prKey('acme', 'widgets', 7))!;
    expect(session.status).toBe('connecting');
    expect(session.chunkLog).toBe('');
  });

  it('stopReview drops a running session and closes its stream', () => {
    startReview(repo, makePR(7));
    const source = lastSource();
    source.emit({ type: 'chunk', text: 'x' });
    stopReview(prKey('acme', 'widgets', 7));
    expect(source.closed).toBe(true);
    expect(get(reviewSessions).has(prKey('acme', 'widgets', 7))).toBe(false);
  });

  it('stopReview is a no-op for finished sessions', () => {
    startReview(repo, makePR(7));
    const source = lastSource();
    source.emit({ type: 'result', review: { findings: [] } });
    source.emit({ type: 'done' });
    stopReview(prKey('acme', 'widgets', 7));
    expect(get(reviewSessions).has(prKey('acme', 'widgets', 7))).toBe(true);
  });
});

describe('hydrateReview', () => {
  it('creates a done session from a cached review', async () => {
    const review = { summary: 'ok', findings: [{ priority: 'P1', title: 'bug', description: 'd' }] };
    vi.mocked(api.get).mockResolvedValue(review);
    await hydrateReview(repo, makePR(7));
    const session = get(reviewSessions).get(prKey('acme', 'widgets', 7))!;
    expect(session.status).toBe('done');
    expect(session.review).toMatchObject(review);
    expect(api.get).toHaveBeenCalledWith('/review/acme/widgets/7');
  });

  it('is a no-op on 204 (null response)', async () => {
    vi.mocked(api.get).mockResolvedValue(null);
    await hydrateReview(repo, makePR(7));
    expect(get(reviewSessions).has(prKey('acme', 'widgets', 7))).toBe(false);
  });

  it('never clobbers a running session', async () => {
    startReview(repo, makePR(7));
    lastSource().emit({ type: 'chunk', text: 'partial' });
    vi.mocked(api.get).mockResolvedValue({ summary: 'cached', findings: [] });
    await hydrateReview(repo, makePR(7));
    const session = get(reviewSessions).get(prKey('acme', 'widgets', 7))!;
    expect(session.status).toBe('streaming');
    expect(session.chunkLog).toBe('partial');
    // A running review shortcuts before hitting the network.
    expect(api.get).not.toHaveBeenCalled();
  });
});

describe('hydrateRepoReviews', () => {
  it('inserts done sessions for cached PRs present in the list', async () => {
    vi.mocked(api.get).mockResolvedValue({
      reviews: [
        { pr_number: 1, summary: 'one', findings: [] },
        { pr_number: 2, summary: 'two', findings: [{ priority: 'P2', title: 't', description: 'd' }] },
      ],
    });
    await hydrateRepoReviews(repo, [makePR(1), makePR(2)]);
    const map = get(reviewSessions);
    expect(map.get(prKey('acme', 'widgets', 1))!.status).toBe('done');
    expect(map.get(prKey('acme', 'widgets', 2))!.review!.findings).toHaveLength(1);
    // The pr_number marker must not leak into the stored review.
    expect('pr_number' in (map.get(prKey('acme', 'widgets', 1))!.review as object)).toBe(false);
  });

  it('skips cached reviews for PRs not in the current list', async () => {
    vi.mocked(api.get).mockResolvedValue({
      reviews: [{ pr_number: 99, summary: 'gone', findings: [] }],
    });
    await hydrateRepoReviews(repo, [makePR(1)]);
    expect(get(reviewSessions).has(prKey('acme', 'widgets', 99))).toBe(false);
  });

  it('never overwrites an existing session', async () => {
    startReview(repo, makePR(1));
    lastSource().emit({ type: 'chunk', text: 'live' });
    vi.mocked(api.get).mockResolvedValue({
      reviews: [{ pr_number: 1, summary: 'cached', findings: [] }],
    });
    await hydrateRepoReviews(repo, [makePR(1)]);
    const session = get(reviewSessions).get(prKey('acme', 'widgets', 1))!;
    expect(session.status).toBe('streaming');
    expect(session.chunkLog).toBe('live');
  });
});
