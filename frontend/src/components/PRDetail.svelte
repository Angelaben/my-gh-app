<script lang="ts">
  import { activeTab } from '../stores/prs';
  import type { Repo, PR } from '../lib/types';
  import ReviewTab from './ReviewTab.svelte';
  import CommentsTab from './CommentsTab.svelte';

  let { repo, pr }: { repo: Repo; pr: PR } = $props();
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
    <button class="tab" class:active={$activeTab === 'comments'} onclick={() => activeTab.set('comments')}>Comments</button>
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
</style>
