<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { connectReviewStream } from '../lib/sse';
  import { cachedReview } from '../stores/prs';
  import { showToast, selectedModel, aiProvider } from '../stores/ui';
  import type { Finding, Repo, PR, SSEReviewEvent } from '../lib/types';
  import FindingCard from './FindingCard.svelte';

  let { repo, pr, rerun = false, oncomplete }: { repo: Repo; pr: PR; rerun?: boolean; oncomplete?: () => void } = $props();

  type Status = 'connecting' | 'streaming' | 'done' | 'error';

  let status = $state<Status>('connecting');
  let findings = $state<Finding[]>([]);
  let chunkLog = $state('');
  let errorMsg = $state('');
  let lastStep = $state('');
  let warnings = $state<string[]>([]);
  let showWarnings = $state(false);
  let cleanup: (() => void) | null = null;

  /** Extract a human-readable step from a raw opencode output line. */
  function extractStep(text: string): string | null {
    const line = text.trim();
    if (!line || line.startsWith('{') || line.startsWith('[')) return null;  // skip JSON
    // Opencode step lines: "> build · claude-sonnet-4.6", "✓ read file", etc.
    if (/^[>✓✗·]/.test(line)) return line;
    // Tool calls: "Reading file app/main.py", "Searching for ..."
    if (/^(Reading|Writing|Searching|Running|Executing|Editing|Analyzing)\b/i.test(line)) return line;
    // Substantial prose lines (AI thinking/output)
    if (line.length > 20 && line.length < 120 && !/^\s*[{}[\]",]/.test(line)) return line;
    return null;
  }

  onMount(() => {
    cleanup = connectReviewStream(repo.owner, repo.name, pr.number, handleEvent, rerun, $selectedModel || undefined);
  });

  onDestroy(() => cleanup?.());

  function handleEvent(event: SSEReviewEvent) {
    if (event.type === 'chunk') {
      status = 'streaming';
      chunkLog += event.text;
      // Update live step from any meaningful line in this chunk
      for (const line of event.text.split('\n')) {
        const step = extractStep(line);
        if (step) lastStep = step;
      }
    } else if (event.type === 'result') {
      findings = event.review.findings;
      cachedReview.set(event.review);
      status = 'done';
      oncomplete?.();
    } else if (event.type === 'warning') {
      warnings = [...warnings, ...event.lines];
    } else if (event.type === 'done') {
      if (status !== 'done') { status = 'done'; oncomplete?.(); }
    } else if (event.type === 'error') {
      errorMsg = event.message;
      status = 'error';
      showToast(event.message, 'error');
    }
  }

  const PROVIDER_LABELS: Record<string, string> = {
    'claude-code': 'Claude Code',
    'opencode': 'OpenCode',
  };
  const providerLabel = $derived(PROVIDER_LABELS[$aiProvider] ?? ($aiProvider || 'AI provider'));
  const statusLabel = $derived<Record<Status, string>>({
    connecting: `Connecting to ${providerLabel}…`,
    streaming: 'Analyzing diff…',
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
        {#if status === 'streaming' && lastStep}
          <span class="status-step" title={lastStep}>{lastStep}</span>
        {:else if status === 'connecting'}
          <span class="status-step">Waiting for response…</span>
        {/if}
      </div>
    </div>
    <div class="header-right">
      {#if status === 'connecting' || status === 'streaming'}
        <button class="btn btn-danger btn-sm" onclick={() => { cleanup?.(); status = 'done'; }}>⏹ Stop</button>
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

  {#if status === 'connecting' || status === 'streaming'}
    <div class="progress-bar"><div class="progress-fill indeterminate"></div></div>
  {:else if status === 'done'}
    <div class="progress-bar"><div class="progress-fill full"></div></div>
  {/if}

  {#if status === 'error'}
    <div class="error-box">{errorMsg}</div>
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
  .progress-fill { height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-hover)); }
  .progress-fill.full { width: 100%; background: var(--success); transition: background 0.4s; }

  @keyframes indeterminate {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(400%); }
  }
  .indeterminate { width: 25%; animation: indeterminate 1.4s ease infinite; }

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

  .raw-log { margin-top: 10px; }
  .raw-log summary { font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.06em; cursor: pointer; }
  .raw-log summary:hover { color: var(--text-secondary); }
  .log-body { margin-top: 8px; font-size: 10px; color: var(--text-muted); background: rgba(0,0,0,0.3); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 10px 12px; white-space: pre-wrap; overflow-x: auto; max-height: 300px; overflow-y: auto; line-height: 1.6; }
</style>
