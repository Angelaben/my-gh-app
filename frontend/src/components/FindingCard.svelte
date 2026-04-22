<script lang="ts">
  import type { Finding, Repo, PR } from '../lib/types';
  import { publishFinding } from '../stores/prs';
  import { showToast } from '../stores/ui';

  let { finding, repo, pr, index = 0 }: { finding: Finding; repo: Repo; pr: PR; index?: number } = $props();

  let publishing = $state(false);
  let published = $state(false);

  async function handlePublish() {
    publishing = true;
    try {
      await publishFinding(repo.owner, repo.name, pr.number, finding);
      published = true;
      showToast('Published to PR', 'success');
    } catch {
      showToast('Failed to publish', 'error');
    } finally {
      publishing = false;
    }
  }
</script>

<div
  class="finding finding-{finding.priority.toLowerCase()}"
  style="animation-delay: {index * 60}ms"
>
  <div class="finding-header">
    <span class="badge badge-{finding.priority.toLowerCase()}">{finding.priority}</span>
    <span class="finding-title">{finding.title}</span>
    {#if finding.file}
      <span class="file-ref">{finding.file}{finding.line ? `:${finding.line}` : ''}</span>
    {/if}
    <button
      class="btn btn-sm {published ? 'btn-success' : 'btn-accent'} publish-btn"
      onclick={handlePublish}
      disabled={publishing || published}
    >
      {publishing ? '…' : published ? '✓ Published' : '↗ Publish'}
    </button>
  </div>

  <p class="finding-desc">{finding.description}</p>

  {#if finding.suggestion}
    <details class="suggestion">
      <summary>Suggestion</summary>
      <pre class="suggestion-body">{finding.suggestion}</pre>
    </details>
  {/if}
</div>

<style>
  .finding {
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-md);
    padding: 12px 14px;
    margin-bottom: 6px;
    position: relative;
    overflow: hidden;
    animation: fadeSlide 0.3s ease both;
    transition: border-color var(--transition-base), box-shadow var(--transition-base);
  }
  .finding::before {
    content: '';
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 2px;
  }
  :global(.finding-p0)::before { background: var(--p0); box-shadow: var(--glow-p0); }
  :global(.finding-p1)::before { background: var(--p1); box-shadow: var(--glow-p1); }
  :global(.finding-p2)::before { background: var(--p2); }
  :global(.finding-p3)::before { background: var(--p3); }
  :global(.finding-p0):hover { border-color: rgba(255,59,59,0.25); box-shadow: var(--glow-p0); }
  :global(.finding-p1):hover { border-color: rgba(255,140,66,0.25); box-shadow: var(--glow-p1); }
  :global(.finding-p2):hover { border-color: rgba(255,209,102,0.2); box-shadow: var(--glow-p2); }
  :global(.finding-p3):hover { border-color: rgba(126,200,227,0.2); box-shadow: var(--glow-p3); }
  .finding-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
  .finding-title { font-size: 12px; font-weight: 600; color: var(--text-primary); flex: 1; min-width: 0; }
  .file-ref { font-size: 10px; color: var(--text-muted); font-style: italic; white-space: nowrap; }
  .publish-btn { margin-left: auto; flex-shrink: 0; }
  .finding-desc { font-size: 12px; color: var(--text-secondary); line-height: 1.6; margin: 0; }
  .suggestion { margin-top: 10px; }
  .suggestion summary { font-size: 10px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.06em; cursor: pointer; user-select: none; }
  .suggestion summary:hover { color: var(--text-secondary); }
  .suggestion-body { margin-top: 8px; font-size: 11px; color: var(--text-secondary); background: rgba(0,0,0,0.25); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 10px 12px; white-space: pre-wrap; overflow-x: auto; line-height: 1.5; }
</style>
