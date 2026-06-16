<script lang="ts">
  import type { PR, Repo } from '../lib/types';
  import { reviewSessions, prKey } from '../stores/reviewSessions';
  import { selectionMode, selectedPRNumbers, toggleSelected } from '../stores/reviewQueue';

  let { pr, repo, onselect }: { pr: PR; repo: Repo; onselect: () => void } = $props();

  const selecting = $derived($selectionMode);
  const checked = $derived($selectedPRNumbers.has(pr.number));

  function handleClick() {
    if (selecting) toggleSelected(pr.number);
    else onselect();
  }

  const session = $derived($reviewSessions.get(prKey(repo.owner, repo.name, pr.number)));
  const reviewState = $derived(
    !session
      ? null
      : session.status === 'connecting' || session.status === 'streaming'
        ? 'running'
        : session.status === 'error'
          ? 'error'
          : session.review
            ? 'done'
            : null
  );

  function formatDate(iso: string) {
    return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  }
</script>

<button class="pr-item" class:selecting onclick={handleClick}>
  <div class="pr-top">
    {#if selecting}
      <span class="chk" class:on={checked} aria-hidden="true"></span>
    {/if}
    <span class="pr-number">#{pr.number}</span>
    <span class="pr-status {pr.is_draft ? 'status-draft' : 'status-ready'}">
      {pr.is_draft ? 'DRAFT' : 'READY'}
    </span>
    {#if reviewState === 'running'}
      <span class="pr-status review-running" title="AI review running in the background">
        <span class="mini-spinner"></span> REVIEWING
      </span>
    {:else if reviewState === 'done'}
      <span class="pr-status review-done" title="AI review complete">
        ✓ {session?.review?.findings.length ?? 0} FINDING{(session?.review?.findings.length ?? 0) === 1 ? '' : 'S'}
      </span>
    {:else if reviewState === 'error'}
      <span class="pr-status review-failed" title={session?.errorMsg || 'AI review failed'}>✕ FAILED</span>
    {/if}
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
  .pr-item.selecting:hover { border-color: rgba(255,107,53,0.5); }
  .pr-top { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .chk {
    width: 15px; height: 15px; flex-shrink: 0; border-radius: 3px;
    border: 1px solid var(--border-active); position: relative;
  }
  .chk.on { background: color-mix(in srgb, var(--accent) 22%, transparent); border-color: var(--accent); }
  .chk.on::after {
    content: '✓'; position: absolute; inset: -3px 0 0 1px;
    color: var(--accent); font-size: 11px; font-weight: 800;
  }
  .pr-status {
    font-size: 9px; font-weight: 800; padding: 2px 5px;
    border-radius: 3px; flex-shrink: 0; letter-spacing: 0.04em;
  }
  .status-draft {
    background: color-mix(in srgb, var(--text-muted) 14%, transparent);
    color: var(--text-muted);
    border: 1px solid color-mix(in srgb, var(--text-muted) 28%, transparent);
  }
  .status-ready {
    background: color-mix(in srgb, var(--success) 12%, transparent);
    color: var(--success);
    border: 1px solid color-mix(in srgb, var(--success) 28%, transparent);
  }
  .review-running {
    display: inline-flex; align-items: center; gap: 4px;
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    color: var(--accent);
    border: 1px solid color-mix(in srgb, var(--accent) 32%, transparent);
  }
  .review-done {
    background: color-mix(in srgb, var(--success) 12%, transparent);
    color: var(--success);
    border: 1px solid color-mix(in srgb, var(--success) 28%, transparent);
  }
  .review-failed {
    background: color-mix(in srgb, var(--p0) 12%, transparent);
    color: var(--p0);
    border: 1px solid color-mix(in srgb, var(--p0) 30%, transparent);
  }
  .mini-spinner {
    width: 8px; height: 8px;
    border: 1.5px solid color-mix(in srgb, var(--accent) 30%, transparent);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    flex-shrink: 0;
  }
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
