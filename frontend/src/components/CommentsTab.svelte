<script lang="ts">
  import { onMount } from 'svelte';
  import { comments, loadComments, analyzeComments } from '../stores/prs';
  import { showToast } from '../stores/ui';
  import type { Repo, PR } from '../lib/types';
  import CommentCard from './CommentCard.svelte';

  let { repo, pr }: { repo: Repo; pr: PR } = $props();

  let loading = $state(true);
  let analyzing = $state(false);
  let filterAuthor = $state('');
  let filterPriority = $state('');

  onMount(async () => {
    try {
      await loadComments(repo.owner, repo.name, pr.number);
      // Auto-analyze comments on load
      await analyzeComments(repo.owner, repo.name, pr.number);
    } catch {
      showToast('Failed to load comments', 'error');
    } finally {
      loading = false;
    }
  });

  async function handleAnalyze() {
    analyzing = true;
    try {
      await analyzeComments(repo.owner, repo.name, pr.number);
      showToast('Analysis complete', 'success');
    } catch {
      showToast('Analysis failed', 'error');
    } finally {
      analyzing = false;
    }
  }

  const authors = $derived([...new Set($comments.map((c) => c.author))]);
  const hasAnalysis = $derived($comments.some((c) => c.analysis));

  const filtered = $derived(
    $comments.filter((c) => {
      if (filterAuthor && c.author !== filterAuthor) return false;
      if (filterPriority && c.analysis?.priority !== filterPriority) return false;
      return true;
    })
  );
</script>

<div class="comments-tab">
  <div class="toolbar">
    <button class="btn btn-accent" onclick={handleAnalyze} disabled={analyzing || loading}>
      {analyzing ? '…' : '⚙ Analyze Comments'}
    </button>
    <select class="filter-select" bind:value={filterAuthor}>
      <option value="">All authors</option>
      {#each authors as author}
        <option value={author}>{author}</option>
      {/each}
    </select>
    <select class="filter-select" bind:value={filterPriority} disabled={!hasAnalysis}>
      <option value="">All severities</option>
      <option value="P0">P0</option>
      <option value="P1">P1</option>
      <option value="P2">P2</option>
      <option value="P3">P3</option>
    </select>
    <span class="count">{filtered.length} / {$comments.length}</span>
  </div>

  {#if loading}
    <div class="loading-state">
      <div class="spinner"></div>
      <span style="color:var(--text-muted)">Loading comments…</span>
    </div>
  {:else if filtered.length === 0}
    <div class="empty-state-inline">No comments match filters.</div>
  {:else}
    {#each filtered as comment (comment.id)}
      <CommentCard {comment} {repo} {pr} />
    {/each}
  {/if}
</div>

<style>
  .comments-tab { display: flex; flex-direction: column; gap: 10px; }

  .toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .filter-select {
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 5px 8px;
    cursor: pointer;
    transition: border-color var(--transition-fast);
  }
  .filter-select:focus { outline: none; border-color: var(--border-active); }
  .filter-select:disabled { opacity: 0.4; cursor: default; }

  .count { font-size: 10px; color: var(--text-muted); margin-left: auto; }

  .loading-state {
    display: flex; align-items: center; gap: 10px; padding: 40px 0; justify-content: center;
  }
  .empty-state-inline { color: var(--text-muted); padding: 40px 0; text-align: center; font-size: 12px; }
</style>
