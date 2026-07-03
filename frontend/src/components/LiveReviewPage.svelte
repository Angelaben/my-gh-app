<script lang="ts">
  import { onMount } from 'svelte';
  import {
    liveReviewStatus,
    liveReviewEvents,
    initLiveReview,
    startLiveReview,
    stopLiveReview,
  } from '../stores/liveReview';
  import { openSettings } from '../stores/settings';
  import { showToast } from '../stores/ui';
  import type { LiveReviewEventKind } from '../lib/types';

  let busy = $state(false);
  let feedEl = $state<HTMLElement | null>(null);

  const running = $derived($liveReviewStatus?.running ?? false);

  async function toggle(): Promise<void> {
    busy = true;
    try {
      if (running) await stopLiveReview();
      else await startLiveReview();
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Live review request failed', 'error');
    } finally {
      busy = false;
    }
  }

  onMount(() => { void initLiveReview(); });

  $effect(() => {
    void $liveReviewEvents.length;
    if (feedEl) feedEl.scrollTop = feedEl.scrollHeight;
  });

  const fmtTs = (ts?: string | null) =>
    ts ? ts.replace('T', ' ').replace(/\.\d+.*$/, '').replace('Z', '') : '—';
  const fmtInterval = (s?: number) => {
    if (!s) return '—';
    if (s % 60 === 0) return `${s / 60} min`;
    return `${s}s`;
  };

  const KIND_LABELS: Record<LiveReviewEventKind, string> = {
    started: 'START',
    stopped: 'STOP',
    poll: 'POLL',
    review_triggered: 'TRIGGER',
    review_started: 'REVIEW',
    review_finished: 'DONE',
    review_failed: 'FAILED',
    error: 'ERROR',
  };
</script>

<div class="live-page">
  <h1 class="page-title">Live Review</h1>

  <section class="panel control-panel">
    <div class="control-row">
      <button
        class="toggle-btn"
        class:running
        onclick={() => void toggle()}
        disabled={busy || $liveReviewStatus === null}
      >
        {#if busy}…{:else if running}■ Stop live review{:else}▶ Start live review{/if}
      </button>
      <span class="status-dot" class:on={running}></span>
      <span class="status-label">
        {#if $liveReviewStatus === null}
          connecting…
        {:else if running}
          watching — polls every {fmtInterval($liveReviewStatus.poll_interval)}
        {:else}
          off — not polling GitHub
        {/if}
      </span>
    </div>
    <div class="control-meta">
      <span>Last poll <b>{fmtTs($liveReviewStatus?.last_poll_at)}</b></span>
      <span>
        Interval <b>{fmtInterval($liveReviewStatus?.poll_interval)}</b>
        <button class="link-btn" onclick={() => void openSettings()}>configure</button>
      </span>
      {#if ($liveReviewStatus?.active_reviews.length ?? 0) > 0}
        <span class="in-flight">
          {$liveReviewStatus?.active_reviews.length} review(s) in flight:
          {$liveReviewStatus?.active_reviews.join(', ')}
        </span>
      {/if}
    </div>
    <p class="note">
      ⓘ While started, every registered repo is polled for open non-draft PRs. A PR is
      auto-reviewed when it has no review yet, or when new commits landed since its last
      review. Results appear in each PR's Review tab as usual. Stopping only stops the
      polling — reviews already in flight run to completion.
    </p>
  </section>

  <section class="panel feed-panel">
    <h2 class="panel-title">Activity feed</h2>
    <div class="feed-body" bind:this={feedEl}>
      {#if $liveReviewEvents.length === 0}
        <div class="empty">No activity yet — start live review and events will appear here.</div>
      {:else}
        {#each $liveReviewEvents as event (event.seq)}
          <div class="feed-line kind-{event.kind}">
            <span class="feed-ts">{fmtTs(event.ts).slice(11)}</span>
            <span class="feed-kind">{KIND_LABELS[event.kind] ?? event.kind}</span>
            {#if event.repo}<span class="feed-target">{event.repo}{event.pr != null ? `#${event.pr}` : ''}</span>{/if}
            <span class="feed-msg">{event.message}</span>
          </div>
        {/each}
      {/if}
    </div>
  </section>
</div>

<style>
  .live-page { padding: 20px; display: flex; flex-direction: column; gap: 14px; height: 100%; box-sizing: border-box; }
  .page-title { font-size: 15px; font-weight: 700; color: var(--text-primary); letter-spacing: 0.04em; }

  .panel {
    background: var(--glass-bg); border: 1px solid var(--glass-border);
    border-radius: var(--radius-md); padding: 12px 14px;
  }
  .panel-title { font-size: 11px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.06em; }
  .empty { color: var(--text-muted); font-size: 11px; padding: 16px 0; text-align: center; }

  .control-panel { display: flex; flex-direction: column; gap: 10px; }
  .control-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .toggle-btn {
    padding: 8px 16px; border-radius: var(--radius-sm);
    border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    color: var(--accent); font-family: var(--font-mono); font-size: 12px; font-weight: 600;
    cursor: pointer; transition: all var(--transition-fast);
  }
  .toggle-btn:hover:not(:disabled) { background: color-mix(in srgb, var(--accent) 22%, transparent); }
  .toggle-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .toggle-btn.running {
    border-color: color-mix(in srgb, var(--p0) 40%, transparent);
    background: color-mix(in srgb, var(--p0) 12%, transparent);
    color: var(--p0);
  }
  .toggle-btn.running:hover:not(:disabled) { background: color-mix(in srgb, var(--p0) 22%, transparent); }

  .status-dot {
    width: 9px; height: 9px; border-radius: 50%;
    background: var(--text-muted); flex-shrink: 0;
  }
  .status-dot.on { background: var(--p3); box-shadow: 0 0 6px var(--p3); animation: pulse 2s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: 0.4; } }
  .status-label { color: var(--text-secondary); font-size: 12px; }

  .control-meta { display: flex; gap: 18px; flex-wrap: wrap; color: var(--text-muted); font-size: 11px; }
  .control-meta b { color: var(--text-primary); font-family: var(--font-mono); font-weight: 600; }
  .link-btn {
    background: none; border: none; padding: 0; margin-left: 4px;
    color: var(--accent); font-size: 11px; cursor: pointer; text-decoration: underline;
  }
  .in-flight { color: var(--p2); }
  .note { color: var(--text-muted); font-size: 11px; margin: 0; line-height: 1.5; }

  .feed-panel { display: flex; flex-direction: column; flex: 1; min-height: 0; }
  .feed-body {
    background: rgba(0,0,0,0.3); border: 1px solid var(--border); border-radius: var(--radius-sm);
    flex: 1; min-height: 240px; overflow-y: auto; padding: 8px 10px; margin-top: 8px;
    font-family: var(--font-mono); font-size: 10px; line-height: 1.7;
  }
  .feed-line { display: flex; gap: 8px; white-space: pre-wrap; word-break: break-word; }
  .feed-ts { color: var(--text-muted); opacity: 0.6; flex-shrink: 0; }
  .feed-kind { flex-shrink: 0; min-width: 62px; font-weight: 600; color: var(--text-muted); }
  .kind-started .feed-kind, .kind-review_finished .feed-kind { color: var(--p3); }
  .kind-review_triggered .feed-kind, .kind-review_started .feed-kind { color: var(--accent); }
  .kind-stopped .feed-kind { color: var(--p2); }
  .kind-review_failed .feed-kind, .kind-error .feed-kind { color: var(--p0); }
  .feed-target { color: var(--accent); opacity: 0.8; flex-shrink: 0; }
  .feed-msg { color: var(--text-secondary); }
</style>
