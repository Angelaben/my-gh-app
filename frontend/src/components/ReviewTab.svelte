<script lang="ts">
  import { cachedReview } from '../stores/prs';
  import type { Repo, PR } from '../lib/types';
  import StreamingReview from './StreamingReview.svelte';
  import FindingCard from './FindingCard.svelte';

  let { repo, pr }: { repo: Repo; pr: PR } = $props();

  type ViewMode = 'idle' | 'streaming' | 'results';

  let mode = $state<ViewMode>('idle');
  let rerun = $state(false);

  // Transition idle → results when a cached review is already present on mount.
  $effect(() => {
    if ($cachedReview && mode === 'idle') {
      mode = 'results';
    }
  });

  function startReview(isRerun = false) {
    rerun = isRerun;
    mode = 'streaming';
    cachedReview.set(null);
  }

  function handleReviewComplete() {
    mode = 'results';
  }
</script>

<div class="review-tab">
  <div class="actions">
    {#if mode === 'idle'}
      <button class="btn btn-accent" onclick={() => startReview(false)}>▶ Run Review</button>
    {:else if mode === 'results'}
      <button class="btn btn-sm" onclick={() => startReview(true)}>↻ Re-run</button>
      <span class="cache-note">Showing cached review</span>
    {:else if mode === 'streaming'}
      <span style="color:var(--text-muted);font-size:12px">Review in progress…</span>
    {/if}
  </div>

  {#if mode === 'streaming'}
    <StreamingReview {repo} {pr} {rerun} oncomplete={handleReviewComplete} />
  {:else if mode === 'results' && $cachedReview}
    <div class="findings-list">
      {#each $cachedReview.findings as finding, i (i)}
        <FindingCard {finding} {repo} {pr} index={i} />
      {/each}
    </div>
  {:else if mode === 'idle'}
    <div class="idle-state">No review yet. Run the review to analyze this PR.</div>
  {/if}
</div>

<style>
  .review-tab { display: flex; flex-direction: column; gap: 14px; }
  .actions { display: flex; align-items: center; gap: 10px; }
  .cache-note { font-size: 10px; color: var(--text-muted); }
  .findings-list { display: flex; flex-direction: column; gap: 0; }
  .idle-state { padding: 40px 0; text-align: center; color: var(--text-muted); font-size: 12px; }
</style>
