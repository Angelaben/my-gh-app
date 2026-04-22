<script lang="ts">
  import type { PR } from '../lib/types';

  let { pr, onselect }: { pr: PR; onselect: () => void } = $props();

  function formatDate(iso: string) {
    return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  }
</script>

<button class="pr-item" onclick={onselect}>
  <div class="pr-top">
    <span class="pr-number">#{pr.number}</span>
    <span class="pr-title">{pr.title}</span>
  </div>
  <div class="pr-meta">
    <span class="meta-item">{pr.author}</span>
    <span class="meta-sep">·</span>
    <span class="meta-item branch">{pr.branch}</span>
    <span class="meta-sep">·</span>
    <span class="meta-item additions">+{pr.additions}</span>
    <span class="meta-item deletions">-{pr.deletions}</span>
    <span class="meta-sep meta-right">·</span>
    <span class="meta-item date">{formatDate(pr.updated_at)}</span>
  </div>
</button>

<style>
  .pr-item {
    display: block; width: 100%;
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-md);
    padding: 12px 14px; cursor: pointer; text-align: left;
    transition: background var(--transition-base), border-color var(--transition-base), transform var(--transition-fast), box-shadow var(--transition-base);
    font-family: var(--font-mono);
    margin-bottom: 6px;
  }
  .pr-item:hover {
    background: var(--glass-bg-hover);
    border-color: var(--glass-border-hover);
    transform: translateY(-1px);
    box-shadow: 0 4px 16px rgba(0,0,0,0.25);
  }
  .pr-top { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
  .pr-number { font-size: 11px; font-weight: 700; color: var(--accent); flex-shrink: 0; }
  .pr-title { font-size: 12px; font-weight: 500; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pr-meta { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
  .meta-item { font-size: 10px; color: var(--text-muted); }
  .meta-sep { font-size: 10px; color: var(--border-active); }
  .meta-right { margin-left: auto; }
  .branch { color: var(--text-secondary); font-style: italic; max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .additions { color: var(--success); }
  .deletions { color: var(--p0); }
  .date { color: var(--text-muted); }
</style>
