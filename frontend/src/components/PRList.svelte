<script lang="ts">
  import { onMount } from 'svelte';
  import { prs, activePR, loadPRs } from '../stores/prs';
  import { showToast } from '../stores/ui';
  import {
    selectionMode,
    selectedPRNumbers,
    enqueueReviews,
    clearSelection,
  } from '../stores/reviewQueue';
  import { reviewSettings } from '../stores/settings';
  import type { Repo, PR } from '../lib/types';
  import PRItem from './PRItem.svelte';

  let { repo }: { repo: Repo } = $props();
  let loading = $state(true);
  let error = $state('');

  onMount(async () => {
    selectionMode.set(false);
    clearSelection();
    try {
      await loadPRs(repo.owner, repo.name, true);
    } catch {
      error = 'Failed to load PRs';
      showToast('Failed to load PRs', 'error');
    } finally {
      loading = false;
    }
  });

  // Auto-refresh the open-PR list on the configured cadence (default 15 min).
  // Silent — no `loading` flip — so the list doesn't flash on each cycle.
  $effect(() => {
    const seconds = $reviewSettings?.pr_list_refresh_interval;
    if (!seconds || seconds <= 0) return;
    const id = setInterval(() => {
      loadPRs(repo.owner, repo.name, true).catch(() => {
        /* transient — the next cycle (or manual refresh) retries */
      });
    }, seconds * 1000);
    return () => clearInterval(id);
  });

  function toggleSelectMode() {
    selectionMode.update((v) => !v);
    clearSelection();
  }

  function reviewSelected() {
    const chosen = $prs.filter((p) => $selectedPRNumbers.has(p.number));
    if (chosen.length === 0) {
      showToast('Select at least one PR', 'info');
      return;
    }
    enqueueReviews(repo, chosen);
    selectionMode.set(false);
    clearSelection();
    showToast(`Queued ${chosen.length} review${chosen.length === 1 ? '' : 's'}`, 'success');
  }

  async function handleSelect(pr: PR) {
    activePR.set(pr);
  }

  async function handleRefresh() {
    loading = true;
    error = '';
    try {
      await loadPRs(repo.owner, repo.name, true);
    } catch {
      showToast('Failed to refresh', 'error');
    } finally {
      loading = false;
    }
  }
</script>

<div class="pr-list-view">
  <div class="list-header">
    <span class="list-title">Open Pull Requests</span>
    <span class="repo-label">{repo.full_name}</span>
    <button class="btn btn-sm" onclick={handleRefresh} disabled={loading}>
      {loading ? '…' : '↻ Refresh'}
    </button>
    <button class="btn btn-sm" onclick={toggleSelectMode} disabled={loading || $prs.length === 0}>
      {$selectionMode ? '✕ Cancel' : '☑ Select'}
    </button>
    {#if $selectionMode}
      <button
        class="btn btn-sm btn-accent"
        onclick={reviewSelected}
        disabled={$selectedPRNumbers.size === 0}
      >⧉ Review {$selectedPRNumbers.size} selected</button>
    {/if}
  </div>

  {#if loading}
    <div class="loading-state">
      <div class="spinner"></div>
      <span style="color:var(--text-muted)">Loading PRs…</span>
    </div>
  {:else if error}
    <div class="error-state">{error}</div>
  {:else if $prs.length === 0}
    <div class="empty-state-inline">No open pull requests.</div>
  {:else}
    <div class="list">
      {#each $prs as pr (pr.number)}
        <PRItem {pr} {repo} onselect={() => handleSelect(pr)} />
      {/each}
    </div>
  {/if}
</div>

<style>
  .pr-list-view { padding: 20px; }
  .list-header { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
  .list-title { font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-muted); }
  .repo-label { font-size: 11px; color: var(--accent); background: rgba(255,107,53,0.08); border: 1px solid rgba(255,107,53,0.2); border-radius: 3px; padding: 2px 7px; }
  .loading-state { display: flex; align-items: center; gap: 10px; padding: 40px 0; justify-content: center; }
  .error-state { color: var(--p0); padding: 40px 0; text-align: center; }
  .empty-state-inline { color: var(--text-muted); padding: 40px 0; text-align: center; }
</style>
