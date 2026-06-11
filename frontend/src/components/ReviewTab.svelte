<script lang="ts">
  import {
    stagedFindingsByPR,
    publishReviewsModalOpen,
  } from '../stores/prs';
  import { reviewSessions, prKey, startReview } from '../stores/reviewSessions';
  import type { Repo, PR, Finding } from '../lib/types';
  import StreamingReview from './StreamingReview.svelte';
  import FindingCard from './FindingCard.svelte';
  import PublishReviewsModal from './PublishReviewsModal.svelte';

  let { repo, pr }: { repo: Repo; pr: PR } = $props();

  type Priority = 'P0' | 'P1' | 'P2' | 'P3';

  const ALL_PRIORITIES: Priority[] = ['P0', 'P1', 'P2', 'P3'];

  // View mode is derived from the PR's session, never from local state:
  // re-opening this tab while a review runs in the background must land back
  // on the live streaming view, not on the idle "Run Review" page.
  const key = $derived(prKey(repo.owner, repo.name, pr.number));
  const session = $derived($reviewSessions.get(key));
  const running = $derived(session?.status === 'connecting' || session?.status === 'streaming');
  const review = $derived(session?.review ?? null);
  const mode = $derived(
    running ? 'streaming' : session?.status === 'error' ? 'error' : review ? 'results' : 'idle'
  );
  const staged = $derived($stagedFindingsByPR.get(key) ?? []);

  let activePriorities = $state<Set<Priority>>(new Set(ALL_PRIORITIES));

  function togglePriority(p: Priority) {
    const next = new Set(activePriorities);
    if (next.has(p)) {
      // Don't allow deselecting everything — keep at least one.
      if (next.size > 1) next.delete(p);
    } else {
      next.add(p);
    }
    activePriorities = next;
  }

  function selectOnly(p: Priority) {
    activePriorities = new Set([p]);
  }

  function selectAll() {
    activePriorities = new Set(ALL_PRIORITIES);
  }

  let filteredFindings = $derived(
    (review?.findings ?? []).filter((f: Finding) =>
      activePriorities.has(f.priority as Priority)
    )
  );

  let priorityCounts = $derived(() => {
    const counts: Record<Priority, number> = { P0: 0, P1: 0, P2: 0, P3: 0 };
    for (const f of review?.findings ?? []) {
      if (f.priority in counts) counts[f.priority as Priority]++;
    }
    return counts;
  });

  let allSelected = $derived(activePriorities.size === ALL_PRIORITIES.length);

  function runReview(isRerun = false) {
    activePriorities = new Set(ALL_PRIORITIES);
    startReview(repo, pr, isRerun);
  }
</script>

<div class="review-tab">
  <div class="actions">
    {#if mode === 'idle'}
      <button class="btn btn-accent" onclick={() => runReview(false)}>▶ Run Review</button>
    {:else if mode === 'results'}
      <button class="btn btn-sm" onclick={() => runReview(true)}>↻ Re-run</button>
      <span class="cache-note">Showing cached review</span>
    {:else if mode === 'error'}
      <button class="btn btn-sm" onclick={() => runReview(true)}>↻ Retry</button>
    {:else if mode === 'streaming'}
      <span style="color:var(--text-muted);font-size:12px">Review in progress…</span>
    {/if}
  </div>

  {#if mode === 'streaming' || mode === 'error'}
    <StreamingReview {repo} {pr} />
  {:else if mode === 'results' && review}
    <div class="filter-bar">
      <button
        class="filter-btn filter-all"
        class:active={allSelected}
        onclick={selectAll}
        title="Show all priorities"
      >All <span class="filter-count">{review.findings.length}</span></button>
      {#each ALL_PRIORITIES as p (p)}
        {@const count = priorityCounts()[p]}
        <button
          class="filter-btn filter-{p.toLowerCase()}"
          class:active={activePriorities.has(p)}
          onclick={(e) => e.shiftKey ? selectOnly(p) : togglePriority(p)}
          title="{p} — Click to toggle, Shift+click to show only {p}"
          disabled={count === 0}
        >
          {p} <span class="filter-count">{count}</span>
        </button>
      {/each}
    </div>

    <div class="findings-list">
      {#each filteredFindings as finding, i (i)}
        <FindingCard {finding} {repo} {pr} index={i} />
      {/each}
      {#if filteredFindings.length === 0}
        <div class="no-findings">No findings match the selected filters.</div>
      {/if}
    </div>

    <div class="batch-publish">
      <button
        class="btn btn-warn"
        onclick={() => publishReviewsModalOpen.set(true)}
        disabled={staged.length === 0}
        title="Publish all staged findings as a single Request Changes review"
      >
        ⚠ Publish reviews
        {#if staged.length > 0}
          <span class="badge-count">{staged.length}</span>
        {/if}
      </button>
      {#if staged.length === 0}
        <span class="hint">Click <strong>+ Add to review</strong> on findings to stage them.</span>
      {/if}
    </div>
  {:else if mode === 'idle'}
    <div class="idle-state">No review yet. Run the review to analyze this PR.</div>
  {/if}
</div>

<PublishReviewsModal {repo} {pr} />

<style>
  .review-tab { display: flex; flex-direction: column; gap: 14px; }
  .actions { display: flex; align-items: center; gap: 10px; }
  .cache-note { font-size: 10px; color: var(--text-muted); }
  .findings-list { display: flex; flex-direction: column; gap: 0; }
  .idle-state { padding: 40px 0; text-align: center; color: var(--text-muted); font-size: 12px; }
  .no-findings { padding: 24px 0; text-align: center; color: var(--text-muted); font-size: 12px; }

  /* Priority filter bar */
  .filter-bar {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }
  .filter-btn {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
    border-radius: 14px;
    border: 1px solid var(--glass-border);
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    transition: all 0.15s ease;
    user-select: none;
  }
  .filter-btn:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }
  .filter-btn:hover:not(:disabled) {
    border-color: var(--text-secondary);
    color: var(--text-secondary);
  }
  .filter-count {
    font-size: 10px;
    font-weight: 700;
    opacity: 0.7;
  }

  /* Active state per priority */
  .filter-all.active {
    background: rgba(255, 255, 255, 0.08);
    border-color: var(--text-secondary);
    color: var(--text-primary);
  }
  .filter-p0.active {
    background: rgba(255, 59, 59, 0.15);
    border-color: rgba(255, 59, 59, 0.5);
    color: var(--p0);
  }
  .filter-p1.active {
    background: rgba(255, 140, 66, 0.15);
    border-color: rgba(255, 140, 66, 0.5);
    color: var(--p1);
  }
  .filter-p2.active {
    background: rgba(255, 209, 102, 0.12);
    border-color: rgba(255, 209, 102, 0.4);
    color: var(--p2);
  }
  .filter-p3.active {
    background: rgba(126, 200, 227, 0.12);
    border-color: rgba(126, 200, 227, 0.4);
    color: var(--p3);
  }
  .batch-publish {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 16px;
    padding-top: 16px;
    border-top: 1px solid var(--glass-border);
  }
  .batch-publish .hint {
    font-size: 11px;
    color: var(--text-muted);
  }
  .btn-warn {
    background: rgba(255, 140, 66, 0.16);
    border: 1px solid rgba(255, 140, 66, 0.45);
    color: rgb(255, 175, 110);
  }
  .btn-warn:hover:not(:disabled) {
    background: rgba(255, 140, 66, 0.25);
  }
  .btn-warn:disabled { opacity: 0.4; cursor: not-allowed; }
  .badge-count {
    display: inline-block;
    margin-left: 6px;
    background: rgba(255, 140, 66, 0.3);
    color: rgb(255, 200, 150);
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 8px;
    font-weight: 700;
  }
</style>
