<script lang="ts">
  import { reviewQueue, queueOpen, queueActive, stopQueue } from '../stores/reviewQueue';
  import { reviewSessions, type ReviewSession } from '../stores/reviewSessions';

  const pending = $derived($reviewQueue.items);
  const runningRows = $derived(
    $reviewQueue.runningKeys
      .map((k) => $reviewSessions.get(k))
      .filter((s): s is ReviewSession => !!s),
  );
  const liveCount = $derived(
    runningRows.filter((s) => s.status === 'connecting' || s.status === 'streaming').length,
  );
  const total = $derived(pending.length + runningRows.length);

  function statusLabel(s: ReviewSession): string {
    if (s.status === 'connecting') return 'connecting…';
    if (s.status === 'streaming') return s.latestProgress || 'streaming…';
    if (s.status === 'error') return s.errorMsg || 'failed';
    if (s.status === 'done') return `${s.review?.findings.length ?? 0} findings`;
    return s.status;
  }
</script>

{#if $queueActive}
  <div class="queue" class:collapsed={!$queueOpen}>
    <button class="qh" onclick={() => queueOpen.update((v) => !v)}>
      <span class="qh-title">Review queue</span>
      <span class="qh-meta">{liveCount}/{$reviewQueue.concurrency} running · {total} total</span>
      <span class="chev">{$queueOpen ? '▾' : '▸'}</span>
    </button>

    {#if $queueOpen}
      <div class="qbody">
        {#each runningRows as s (s.key)}
          <div class="qrow">
            {#if s.status === 'connecting' || s.status === 'streaming'}
              <span class="spin"></span>
            {:else if s.status === 'done'}
              <span class="dot done"></span>
            {:else}
              <span class="dot err"></span>
            {/if}
            <span class="num">#{s.pr.number}</span>
            <span class="lbl">{statusLabel(s)}</span>
          </div>
        {/each}
        {#each pending as item (item.key)}
          <div class="qrow">
            <span class="dot queued"></span>
            <span class="num">#{item.pr.number}</span>
            <span class="lbl muted">queued</span>
          </div>
        {/each}
        <div class="qfoot">
          <button class="btn-danger" onclick={stopQueue}>⏹ Stop all</button>
        </div>
      </div>
    {/if}
  </div>
{/if}

<style>
  .queue {
    position: fixed; right: 18px; bottom: 18px; z-index: 90;
    width: min(340px, 92vw);
    background: var(--bg-tertiary); border: 1px solid var(--glass-border);
    border-radius: var(--radius-md); box-shadow: 0 12px 32px rgba(0,0,0,0.45);
    font-family: var(--font-mono); overflow: hidden;
  }
  .qh {
    display: flex; align-items: center; gap: 8px; width: 100%;
    padding: 10px 14px; background: none; border: none; cursor: pointer;
    color: var(--text-primary); font-family: inherit; text-align: left;
  }
  .qh:hover { background: var(--bg-hover); }
  .qh-title { font-size: 12px; font-weight: 600; }
  .qh-meta { font-size: 11px; color: var(--text-secondary); }
  .chev { margin-left: auto; color: var(--text-muted); }
  .qbody { padding: 6px 14px 12px; border-top: 1px solid var(--border); max-height: 50vh; overflow-y: auto; }
  .qrow { display: flex; align-items: center; gap: 9px; padding: 6px 0; font-size: 12px; border-top: 1px solid color-mix(in srgb, var(--border) 60%, transparent); }
  .qrow:first-child { border-top: none; }
  .num { color: var(--accent); font-weight: 700; }
  .lbl { color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .lbl.muted, .muted { color: var(--text-muted); }
  .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .dot.queued { background: var(--text-muted); }
  .dot.done { background: var(--success); }
  .dot.err { background: var(--p0); }
  .spin {
    width: 9px; height: 9px; flex-shrink: 0;
    border: 2px solid color-mix(in srgb, var(--accent) 30%, transparent);
    border-top-color: var(--accent); border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  .qfoot { display: flex; justify-content: flex-end; margin-top: 8px; }
  .btn-danger {
    background: rgba(255,59,59,0.14); border: 1px solid rgba(255,59,59,0.4); color: var(--p0);
    border-radius: var(--radius-sm); font-family: inherit; font-size: 11px; padding: 5px 11px; cursor: pointer;
  }
  .btn-danger:hover { background: rgba(255,59,59,0.22); }
</style>
