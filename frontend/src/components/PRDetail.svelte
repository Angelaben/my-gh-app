<script lang="ts">
  import { activeTab, loadComments, newCommentIds } from '../stores/prs';
  import { hydrateReview } from '../stores/reviewSessions';
  import type { Repo, PR } from '../lib/types';
  import ReviewTab from './ReviewTab.svelte';
  import CommentsTab from './CommentsTab.svelte';

  let { repo, pr }: { repo: Repo; pr: PR } = $props();

  // Eagerly load comments when a PR opens so the tab badge can show how many
  // are new since the last visit (loadComments is idempotent per PR).
  $effect(() => {
    loadComments(repo.owner, repo.name, pr.number).catch(() => {});
    // Restore a completed review from the server cache so a page reload lands
    // back on the results view instead of the idle "Run Review" state.
    hydrateReview(repo, pr).catch(() => {});
  });
</script>

<div class="pr-detail">
  <div class="pr-detail-header">
    <h2 class="pr-title">
      <span class="pr-number">#{pr.number}</span>
      {pr.title}
    </h2>
    <div class="pr-meta">
      <span>{pr.author}</span>
      <span class="sep">·</span>
      <span class="branch">{pr.branch} → {pr.base_branch}</span>
      <span class="sep">·</span>
      <span class="additions">+{pr.additions}</span>
      <span class="deletions">-{pr.deletions}</span>
    </div>
  </div>

  <div class="tabs">
    <button class="tab" class:active={$activeTab === 'review'} onclick={() => activeTab.set('review')}>Review</button>
    <button class="tab" class:active={$activeTab === 'comments'} onclick={() => activeTab.set('comments')}>
      Comments{#if $newCommentIds.length > 0}<span class="tab-badge">{$newCommentIds.length}</span>{/if}
    </button>
  </div>

  <div class="tab-content">
    {#if $activeTab === 'review'}
      <ReviewTab {repo} {pr} />
    {:else}
      <CommentsTab {repo} {pr} />
    {/if}
  </div>
</div>

<style>
  .pr-detail { padding: 20px; display: flex; flex-direction: column; gap: 0; }
  .pr-detail-header {
    background: var(--glass-bg); border: 1px solid var(--glass-border);
    border-radius: var(--radius-lg); padding: 14px 18px; margin-bottom: 14px;
  }
  .pr-title {
    font-size: 14px; font-weight: 600; color: var(--text-primary);
    margin-bottom: 6px; display: flex; align-items: baseline; gap: 8px;
  }
  .pr-number { color: var(--accent); font-size: 12px; flex-shrink: 0; }
  .pr-meta { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text-muted); }
  .sep { color: var(--border-active); }
  .branch { color: var(--text-secondary); font-style: italic; }
  .additions { color: var(--success); }
  .deletions { color: var(--p0); }
  .tabs {
    display: flex; gap: 0;
    border-bottom: 1px solid var(--border); margin-bottom: 14px;
  }
  .tab {
    background: none; border: none; border-bottom: 2px solid transparent;
    margin-bottom: -1px; color: var(--text-muted);
    font-family: var(--font-mono); font-size: 12px; font-weight: 600;
    padding: 8px 16px; cursor: pointer;
    transition: color var(--transition-fast), border-color var(--transition-fast);
  }
  .tab:hover { color: var(--text-secondary); }
  .tab.active { color: var(--accent); border-color: var(--accent); }
  .tab-badge {
    display: inline-block; margin-left: 6px;
    font-size: 9px; font-weight: 700; line-height: 1;
    color: var(--success);
    background: color-mix(in srgb, var(--success) 14%, transparent);
    border: 1px solid color-mix(in srgb, var(--success) 40%, transparent);
    border-radius: 8px; padding: 2px 6px;
    vertical-align: 1px;
  }
</style>
