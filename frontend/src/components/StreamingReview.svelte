<script lang="ts">
  import { reviewSessions, prKey, stopReview, type SessionStatus } from '../stores/reviewSessions';
  import { aiProvider } from '../stores/ui';
  import type { Repo, PR } from '../lib/types';
  import FindingCard from './FindingCard.svelte';

  let { repo, pr }: { repo: Repo; pr: PR } = $props();

  // Pure renderer: the SSE connection and all streaming state live in the
  // per-PR session store, so unmounting this component (PR switch, Activity
  // page…) never interrupts the review.
  const key = $derived(prKey(repo.owner, repo.name, pr.number));
  const session = $derived($reviewSessions.get(key));
  const status = $derived<SessionStatus>(session?.status ?? 'connecting');
  const findings = $derived(session?.review?.findings ?? []);
  const chunkLog = $derived(session?.chunkLog ?? '');
  const errorMsg = $derived(session?.errorMsg ?? '');
  const warnings = $derived(session?.warnings ?? []);
  const chunkCount = $derived(session?.chunkCount ?? 0);
  const totalBytes = $derived(session?.totalBytes ?? 0);
  const latestProgress = $derived(session?.latestProgress ?? '');
  const activity = $derived(session?.activity ?? []);
  const requestId = $derived(session?.requestId ?? '');

  let showWarnings = $state(false);
  let consoleOpen = $state(true);
  let consoleEl = $state<HTMLElement | null>(null);

  // Elapsed-time ticker — display only; the session stores start/end times.
  let now = $state(Date.now());
  $effect(() => {
    if (status !== 'connecting' && status !== 'streaming') return;
    const id = setInterval(() => { now = Date.now(); }, 500);
    return () => clearInterval(id);
  });
  const elapsedSec = $derived(
    !session || !session.startTime
      ? 0
      : Math.max(0, ((session.endTime ?? now) - session.startTime) / 1000)
  );

  $effect(() => {
    if (status === 'done') consoleOpen = false;
  });

  // Auto-scroll the console to the latest entry.
  $effect(() => {
    void activity.length;
    if (consoleEl) consoleEl.scrollTop = consoleEl.scrollHeight;
  });

  // Smooth asymptotic progress: 0 → 90% over time, jumps to 100% when done.
  // 1 - e^(-t/45) reaches ~50% at 31s, ~86% at 90s — feels deterministic.
  const progressPct = $derived(
    status === 'done' || status === 'error'
      ? 100
      : Math.min(90, (1 - Math.exp(-elapsedSec / 45)) * 100)
  );

  // Phase detected from the tail of accumulated output.
  // During the connecting phase (no chunks yet), show the latest stderr
  // progress line from OpenCode instead of the static waiting message.
  const phaseLabel = $derived(
    chunkCount === 0
      ? (latestProgress || 'Waiting for first response…')
      : /"findings"/.test(chunkLog.slice(-300)) || /^\s*\[/.test(chunkLog.slice(-60))
        ? 'Generating review…'
        : /^[>✓✗]|\b(Reading|Writing|Searching|Executing)\b/m.test(chunkLog.slice(-400))
          ? 'Reading context…'
          : 'Analyzing diff…'
  );

  const kbReceived = $derived((totalBytes / 1024).toFixed(1));
  const elapsedLabel = $derived(
    elapsedSec < 60
      ? `${Math.floor(elapsedSec)}s`
      : `${Math.floor(elapsedSec / 60)}m${Math.floor(elapsedSec % 60)}s`
  );

  const PROVIDER_LABELS: Record<string, string> = {
    'claude-code': 'Claude Code',
    'opencode': 'OpenCode',
  };
  const providerLabel = $derived(PROVIDER_LABELS[$aiProvider] ?? ($aiProvider || 'AI provider'));
  const statusLabel = $derived<Record<SessionStatus, string>>({
    connecting: `Connecting to ${providerLabel}…`,
    streaming: 'Streaming…',
    done: 'Review complete',
    error: 'Review failed',
  });

  const priorities = ['P0', 'P1', 'P2', 'P3'] as const;
</script>

<div class="streaming-review">
  <div class="stream-header" class:done={status === 'done'} class:error={status === 'error'}>
    <div class="header-left">
      {#if status === 'connecting' || status === 'streaming'}
        <div class="spinner"></div>
      {:else if status === 'done'}
        <span class="status-icon done-icon">✓</span>
      {:else}
        <span class="status-icon error-icon">✕</span>
      {/if}
      <div class="status-text">
        <span class="status-label">{statusLabel[status]}</span>
        {#if status === 'connecting' || status === 'streaming'}
          <span class="status-step">
            {phaseLabel}{#if chunkCount > 0} · {chunkCount} chunks · {kbReceived} KB · {elapsedLabel}{/if}
          </span>
        {/if}
      </div>
    </div>
    <div class="header-right">
      {#if status === 'connecting' || status === 'streaming'}
        <button class="btn btn-danger btn-sm" onclick={() => stopReview(key)}>⏹ Stop</button>
      {/if}
      {#if warnings.length > 0}
        <div class="warning-wrap">
          <button
            class="warn-btn"
            onclick={() => showWarnings = !showWarnings}
            title="{providerLabel} reported warnings — click to view"
          >⚠ {warnings.length}</button>
          {#if showWarnings}
            <div class="warn-panel">
              <div class="warn-panel-header">
                <span>{providerLabel} warnings</span>
                <button class="warn-close" onclick={() => showWarnings = false}>✕</button>
              </div>
              <pre class="warn-body">{warnings.join('\n')}</pre>
            </div>
          {/if}
        </div>
      {/if}
      {#if findings.length > 0}
        <span class="finding-count">{findings.length} finding{findings.length !== 1 ? 's' : ''}</span>
        {#each priorities as p}
          {@const count = findings.filter(f => f.priority === p).length}
          {#if count > 0}
            <span class="badge badge-{p.toLowerCase()}">{p}: {count}</span>
          {/if}
        {/each}
      {/if}
    </div>
  </div>

  {#if status !== 'error'}
    <div class="progress-bar">
      <div class="progress-fill" style="width: {progressPct}%" class:done={status === 'done'}></div>
    </div>
  {/if}

  {#if status === 'error'}
    <div class="error-box">{errorMsg}</div>
  {/if}

  {#if activity.length > 0}
    <details class="agent-console" bind:open={consoleOpen}>
      <summary>
        Agent activity · {activity.length} event{activity.length !== 1 ? 's' : ''}
        {#if requestId}<span class="console-reqid">req {requestId}</span>{/if}
      </summary>
      <div class="console-body" bind:this={consoleEl}>
        {#each activity as entry, i (i)}
          <div class="console-line {entry.kind}">
            <span class="console-offset">{entry.offset}</span>
            <span class="console-text">{entry.text}</span>
          </div>
        {/each}
        {#if status === 'connecting' || status === 'streaming'}
          <div class="console-line cursor">▌</div>
        {/if}
      </div>
    </details>
  {/if}

  {#if findings.length > 0}
    <div class="findings-list">
      {#each findings as finding, i (i)}
        <FindingCard {finding} {repo} {pr} index={i} />
      {/each}
    </div>
  {:else if status === 'streaming' || status === 'connecting'}
    <div class="skeleton-list">
      {#each [0,1,2] as i}
        <div class="skeleton-card" style="opacity: {1 - i * 0.25}; animation-delay: {i * 100}ms"></div>
      {/each}
    </div>
  {/if}

  {#if chunkLog && status === 'done'}
    <details class="raw-log">
      <summary>Raw output</summary>
      <pre class="log-body">{chunkLog}</pre>
    </details>
  {/if}
</div>

<style>
  .streaming-review { display: flex; flex-direction: column; gap: 0; }

  .stream-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 14px;
    background: rgba(255,107,53,0.06);
    border: 1px solid rgba(255,107,53,0.15);
    border-radius: var(--radius-md) var(--radius-md) 0 0;
    border-bottom: none;
    flex-wrap: wrap; gap: 8px;
  }
  .stream-header.done { background: rgba(78,205,196,0.06); border-color: rgba(78,205,196,0.2); }
  .stream-header.error { background: rgba(255,59,59,0.06); border-color: rgba(255,59,59,0.2); }

  .header-left { display: flex; align-items: center; gap: 8px; }
  .header-right { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }

  .status-text { display: flex; flex-direction: column; gap: 2px; }
  .status-label { font-size: 11px; font-weight: 600; color: var(--text-secondary); }
  .status-step {
    font-size: 10px; color: var(--text-muted); font-family: var(--font-mono);
    max-width: 340px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .status-icon { font-size: 12px; font-weight: 700; }
  .done-icon { color: var(--success); }
  .error-icon { color: var(--p0); }
  .finding-count { font-size: 10px; color: var(--text-muted); }

  .progress-bar { height: 2px; background: var(--border); overflow: hidden; border-left: 1px solid rgba(255,107,53,0.15); border-right: 1px solid rgba(255,107,53,0.15); }
  .progress-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--accent), var(--accent-hover));
    transition: width 0.6s ease, background 0.4s;
    min-width: 2px;
  }
  .progress-fill.done { background: var(--success); }

  .findings-list {
    border: 1px solid var(--glass-border); border-top: none;
    border-radius: 0 0 var(--radius-md) var(--radius-md);
    padding: 10px;
    background: rgba(0,0,0,0.15);
  }

  .skeleton-list {
    border: 1px solid var(--glass-border); border-top: none;
    border-radius: 0 0 var(--radius-md) var(--radius-md);
    padding: 10px; display: flex; flex-direction: column; gap: 6px;
  }
  .skeleton-card {
    height: 56px; background: var(--glass-bg);
    border: 1px solid var(--glass-border); border-radius: var(--radius-sm);
    animation: fadeSlide 0.4s ease both;
  }

  .error-box {
    padding: 12px 14px; color: var(--p0); font-size: 12px;
    background: rgba(255,59,59,0.06); border: 1px solid rgba(255,59,59,0.2);
    border-top: none; border-radius: 0 0 var(--radius-md) var(--radius-md);
  }

  .warning-wrap { position: relative; }
  .warn-btn {
    background: rgba(255, 200, 0, 0.12);
    border: 1px solid rgba(255, 200, 0, 0.4);
    border-radius: var(--radius-sm);
    color: #ffc800;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 700;
    padding: 2px 8px;
    cursor: pointer;
    transition: background var(--transition-fast), border-color var(--transition-fast);
  }
  .warn-btn:hover { background: rgba(255, 200, 0, 0.2); border-color: rgba(255, 200, 0, 0.7); }
  .warn-panel {
    position: absolute; top: calc(100% + 6px); right: 0;
    width: 480px; max-height: 280px;
    background: var(--glass-bg); border: 1px solid rgba(255, 200, 0, 0.35);
    border-radius: var(--radius-md); box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    display: flex; flex-direction: column; z-index: 100;
  }
  .warn-panel-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 12px; border-bottom: 1px solid rgba(255,200,0,0.2);
    font-size: 11px; font-weight: 600; color: #ffc800;
  }
  .warn-close {
    background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 12px; padding: 0 2px;
  }
  .warn-close:hover { color: var(--text-primary); }
  .warn-body {
    flex: 1; overflow-y: auto; margin: 0; padding: 10px 12px;
    font-size: 11px; color: var(--text-secondary); font-family: var(--font-mono);
    white-space: pre-wrap; word-break: break-all; line-height: 1.6;
  }

  .agent-console {
    border: 1px solid var(--glass-border); border-top: none;
    background: rgba(0,0,0,0.25);
  }
  .agent-console summary {
    padding: 6px 12px;
    font-size: 10px; font-weight: 600; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: 0.06em;
    cursor: pointer; user-select: none;
    display: flex; align-items: center; gap: 8px;
  }
  .agent-console summary:hover { color: var(--text-secondary); }
  .console-reqid {
    margin-left: auto; text-transform: none; letter-spacing: 0;
    font-family: var(--font-mono); font-weight: 400; color: var(--text-muted); opacity: 0.7;
  }
  .console-body {
    max-height: 180px; overflow-y: auto;
    padding: 6px 12px 8px;
    font-family: var(--font-mono); font-size: 10px; line-height: 1.7;
    border-top: 1px solid var(--glass-border);
  }
  .console-line { display: flex; gap: 10px; white-space: pre-wrap; word-break: break-word; }
  .console-offset { color: var(--text-muted); opacity: 0.55; min-width: 42px; text-align: right; flex-shrink: 0; }
  .console-line.progress .console-text { color: var(--text-secondary); }
  .console-line.lifecycle .console-text { color: var(--accent); }
  .console-line.warning .console-text { color: #ffc800; }
  .console-line.meta .console-text { color: var(--text-muted); font-style: italic; }
  .console-line.cursor { color: var(--accent); animation: blink 1s steps(1) infinite; }
  @keyframes blink { 50% { opacity: 0; } }

  .raw-log { margin-top: 10px; }
  .raw-log summary { font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.06em; cursor: pointer; }
  .raw-log summary:hover { color: var(--text-secondary); }
  .log-body { margin-top: 8px; font-size: 10px; color: var(--text-muted); background: rgba(0,0,0,0.3); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 10px 12px; white-space: pre-wrap; overflow-x: auto; max-height: 300px; overflow-y: auto; line-height: 1.6; }
</style>
