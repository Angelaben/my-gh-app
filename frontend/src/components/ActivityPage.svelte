<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { connectSSE } from '../lib/sse';
  import { api } from '../lib/api';

  // --- Stats (live via /api/stats/stream) ---

  interface StatsSummary {
    total_records: number;
    reviews: {
      count: number;
      avg_duration_ms: number | null;
      total_findings: number;
      findings_by_priority: Record<string, number>;
      by_provider: Record<string, number>;
      failed_chunks: number;
    };
    events: Record<string, number>;
    recent: MetricRecord[];
  }

  interface MetricRecord {
    ts?: string;
    event?: string;
    repo?: string;
    pr?: number;
    provider?: string;
    model?: string | null;
    duration_ms?: number;
    diff_chars?: number;
    chunks?: number;
    failed_chunks?: number;
    findings_total?: number;
    findings_p0?: number;
    findings_p1?: number;
    findings_p2?: number;
    findings_p3?: number;
  }

  let stats = $state<StatsSummary | null>(null);

  // --- Logs (live via /api/logs/stream) ---

  interface LogRecord {
    seq: number;
    ts: string;
    level: string;
    logger: string;
    msg: string;
    request_id?: string;
    [key: string]: unknown;
  }

  const LOG_CAP = 1000;
  let logs = $state<LogRecord[]>([]);
  let paused = $state(false);
  let levelFilter = $state('');
  let textFilter = $state('');
  let logsEl = $state<HTMLElement | null>(null);
  let pendingWhilePaused: LogRecord[] = [];

  const filteredLogs = $derived(
    logs.filter((r) => {
      if (levelFilter && r.level !== levelFilter) return false;
      if (textFilter) {
        const needle = textFilter.toLowerCase();
        const hay = `${r.logger} ${r.msg} ${r.request_id ?? ''}`.toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    })
  );

  function appendLogs(records: LogRecord[]) {
    const next = [...logs, ...records];
    logs = next.length > LOG_CAP ? next.slice(next.length - LOG_CAP) : next;
  }

  function togglePause() {
    paused = !paused;
    if (!paused && pendingWhilePaused.length > 0) {
      appendLogs(pendingWhilePaused);
      pendingWhilePaused = [];
    }
  }

  $effect(() => {
    void filteredLogs.length;
    if (logsEl && !paused) logsEl.scrollTop = logsEl.scrollHeight;
  });

  let cleanups: (() => void)[] = [];

  onMount(async () => {
    try {
      stats = await api.get<StatsSummary>('/stats');
    } catch { /* stream below will retry */ }
    try {
      const initial = await api.get<{ records: LogRecord[]; last_seq: number }>('/logs/recent?limit=200');
      appendLogs(initial.records);
    } catch { /* non-fatal */ }

    cleanups.push(
      connectSSE<{ type: string; stats?: StatsSummary }>('/api/stats/stream', (ev) => {
        if (ev.type === 'stats' && ev.stats) stats = ev.stats;
      }),
      connectSSE<{ type: string; record?: LogRecord }>('/api/logs/stream', (ev) => {
        if (ev.type !== 'log' || !ev.record) return;
        if (logs.some((r) => r.seq === ev.record!.seq)) return;
        if (paused) pendingWhilePaused.push(ev.record);
        else appendLogs([ev.record]);
      }),
    );
  });

  onDestroy(() => { for (const c of cleanups) c(); });

  const priorities = ['P0', 'P1', 'P2', 'P3'] as const;
  const fmtDuration = (ms?: number | null) =>
    ms == null ? '—' : ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
  const fmtTs = (ts?: string) => (ts ? ts.replace('T', ' ').replace('Z', '') : '—');
  const recentRuns = $derived((stats?.recent ?? []).slice().reverse());
</script>

<div class="activity-page">
  <h1 class="page-title">Activity</h1>

  <!-- Metrics cards -->
  <section class="cards">
    <div class="card">
      <span class="card-label">Reviews run</span>
      <span class="card-value">{stats?.reviews.count ?? '—'}</span>
    </div>
    <div class="card">
      <span class="card-label">Avg duration</span>
      <span class="card-value">{fmtDuration(stats?.reviews.avg_duration_ms)}</span>
    </div>
    <div class="card">
      <span class="card-label">Findings</span>
      <span class="card-value">{stats?.reviews.total_findings ?? '—'}</span>
      <div class="card-badges">
        {#each priorities as p}
          {@const count = stats?.reviews.findings_by_priority?.[p] ?? 0}
          {#if count > 0}
            <span class="badge badge-{p.toLowerCase()}">{p}: {count}</span>
          {/if}
        {/each}
      </div>
    </div>
    <div class="card">
      <span class="card-label">By provider</span>
      <div class="card-badges">
        {#each Object.entries(stats?.reviews.by_provider ?? {}) as [provider, count]}
          <span class="provider-chip">{provider}: {count}</span>
        {:else}
          <span class="card-value">—</span>
        {/each}
      </div>
      {#if (stats?.reviews.failed_chunks ?? 0) > 0}
        <span class="failed-note">{stats?.reviews.failed_chunks} failed chunks</span>
      {/if}
    </div>
  </section>

  <!-- Recent runs -->
  <section class="panel">
    <h2 class="panel-title">Recent runs</h2>
    {#if recentRuns.length === 0}
      <div class="empty">No runs recorded yet — run a review and it will appear here.</div>
    {:else}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>When</th><th>Event</th><th>Repo</th><th>PR</th><th>Provider</th>
              <th>Model</th><th>Duration</th><th>Chunks</th><th>Findings</th>
            </tr>
          </thead>
          <tbody>
            {#each recentRuns as run, i (i)}
              <tr>
                <td class="mono">{fmtTs(run.ts)}</td>
                <td>{run.event ?? '—'}</td>
                <td class="mono">{run.repo ?? '—'}</td>
                <td class="mono">{run.pr != null ? `#${run.pr}` : '—'}</td>
                <td>{run.provider ?? '—'}</td>
                <td class="mono model-cell" title={run.model ?? ''}>{run.model ?? 'default'}</td>
                <td class="mono">{fmtDuration(run.duration_ms)}</td>
                <td class="mono">
                  {run.chunks ?? '—'}{#if (run.failed_chunks ?? 0) > 0}<span class="failed-note"> ({run.failed_chunks} ko)</span>{/if}
                </td>
                <td>
                  {#if run.findings_total != null}
                    <span class="findings-cell">
                      {run.findings_total}
                      {#each priorities as p}
                        {@const count = (run as Record<string, unknown>)[`findings_${p.toLowerCase()}`] as number | undefined}
                        {#if count}
                          <span class="badge badge-{p.toLowerCase()}">{p}:{count}</span>
                        {/if}
                      {/each}
                    </span>
                  {:else}—{/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </section>

  <!-- Live logs -->
  <section class="panel logs-panel">
    <div class="panel-header">
      <h2 class="panel-title">Live backend logs</h2>
      <div class="log-controls">
        {#each ['', 'INFO', 'WARNING', 'ERROR'] as lvl}
          <button
            class="filter-chip"
            class:active={levelFilter === lvl}
            onclick={() => levelFilter = lvl}
          >{lvl === '' ? 'All' : lvl}</button>
        {/each}
        <input class="log-search" type="text" placeholder="filter…" bind:value={textFilter} />
        <button class="filter-chip" class:active={paused} onclick={togglePause}>
          {paused ? '▶ Resume' : '⏸ Pause'}
        </button>
      </div>
    </div>
    <div class="log-body" bind:this={logsEl}>
      {#if filteredLogs.length === 0}
        <div class="empty">No log records {logs.length > 0 ? 'match the filters' : 'yet'}.</div>
      {:else}
        {#each filteredLogs as record (record.seq)}
          <div class="log-line level-{record.level.toLowerCase()}">
            <span class="log-ts">{record.ts.slice(11)}</span>
            <span class="log-level">{record.level}</span>
            <span class="log-logger">{record.logger}</span>
            {#if record.request_id}<span class="log-reqid">[{record.request_id}]</span>{/if}
            <span class="log-msg">{record.msg}</span>
          </div>
        {/each}
      {/if}
    </div>
  </section>
</div>

<style>
  .activity-page { padding: 20px; display: flex; flex-direction: column; gap: 14px; }
  .page-title { font-size: 15px; font-weight: 700; color: var(--text-primary); letter-spacing: 0.04em; }

  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
  .card {
    background: var(--glass-bg); border: 1px solid var(--glass-border);
    border-radius: var(--radius-md); padding: 12px 14px;
    display: flex; flex-direction: column; gap: 6px;
  }
  .card-label { font-size: 10px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.06em; }
  .card-value { font-size: 20px; font-weight: 700; color: var(--text-primary); font-family: var(--font-mono); }
  .card-badges { display: flex; gap: 4px; flex-wrap: wrap; }
  .provider-chip {
    font-size: 10px; font-family: var(--font-mono); color: var(--text-secondary);
    border: 1px solid var(--glass-border); border-radius: 10px; padding: 2px 8px;
  }
  .failed-note { font-size: 10px; color: var(--p1); }

  .panel {
    background: var(--glass-bg); border: 1px solid var(--glass-border);
    border-radius: var(--radius-md); padding: 12px 14px;
  }
  .panel-title { font-size: 11px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.06em; }
  .panel-header { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
  .empty { color: var(--text-muted); font-size: 11px; padding: 16px 0; text-align: center; }

  .table-wrap { overflow-x: auto; margin-top: 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 11px; }
  th {
    text-align: left; color: var(--text-muted); font-size: 9px; text-transform: uppercase;
    letter-spacing: 0.06em; padding: 6px 8px; border-bottom: 1px solid var(--glass-border);
    white-space: nowrap;
  }
  td { padding: 6px 8px; border-bottom: 1px solid color-mix(in srgb, var(--glass-border) 50%, transparent); color: var(--text-secondary); white-space: nowrap; }
  .mono { font-family: var(--font-mono); font-size: 10px; }
  .model-cell { max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
  .findings-cell { display: inline-flex; gap: 4px; align-items: center; }

  .logs-panel { display: flex; flex-direction: column; }
  .log-controls { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .filter-chip {
    padding: 3px 10px; border-radius: 12px;
    border: 1px solid var(--glass-border); background: transparent;
    color: var(--text-muted); font-family: var(--font-mono); font-size: 10px;
    cursor: pointer; transition: all var(--transition-fast);
  }
  .filter-chip:hover { color: var(--text-secondary); border-color: var(--glass-border-hover); }
  .filter-chip.active {
    color: var(--accent);
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    border-color: color-mix(in srgb, var(--accent) 40%, transparent);
  }
  .log-search {
    background: rgba(0,0,0,0.25); border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm); color: var(--text-primary);
    font-family: var(--font-mono); font-size: 10px; padding: 4px 8px; width: 140px;
  }
  .log-search:focus { outline: none; border-color: var(--border-active); }

  .log-body {
    background: rgba(0,0,0,0.3); border: 1px solid var(--border); border-radius: var(--radius-sm);
    height: 320px; overflow-y: auto; padding: 8px 10px;
    font-family: var(--font-mono); font-size: 10px; line-height: 1.7;
  }
  .log-line { display: flex; gap: 8px; white-space: pre-wrap; word-break: break-word; }
  .log-ts { color: var(--text-muted); opacity: 0.6; flex-shrink: 0; }
  .log-level { flex-shrink: 0; min-width: 52px; font-weight: 600; }
  .level-info .log-level { color: var(--text-muted); }
  .level-warning .log-level { color: #ffc800; }
  .level-error .log-level, .level-critical .log-level { color: var(--p0); }
  .log-logger { color: var(--text-muted); flex-shrink: 0; max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
  .log-reqid { color: var(--accent); opacity: 0.7; flex-shrink: 0; }
  .log-msg { color: var(--text-secondary); }
</style>
