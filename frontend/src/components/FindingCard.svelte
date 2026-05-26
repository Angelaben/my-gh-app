<script lang="ts">
  import type { Finding, Repo, PR } from '../lib/types';
  import {
    publishFinding,
    stageFinding,
    unstageFinding,
    stagedReviewFindings,
    formatFindingBody,
    setFindingBodyOverride,
    clearFindingBodyOverride,
    findingBodyOverrides,
  } from '../stores/prs';
  import { showToast } from '../stores/ui';

  let { finding, repo, pr, index = 0 }: { finding: Finding; repo: Repo; pr: PR; index?: number } = $props();

  let publishing = $state(false);
  let published = $state(false);
  let editing = $state(false);
  let editBody = $state('');

  let isStaged = $derived(
    $stagedReviewFindings.some(
      (f) =>
        f.title === finding.title &&
        f.priority === finding.priority &&
        f.file === finding.file &&
        f.line === finding.line,
    ),
  );

  let hasOverride = $derived(
    $findingBodyOverrides.has(`${finding.priority}:${finding.title}:${finding.file ?? ''}:${finding.line ?? ''}`)
  );

  async function handlePublish() {
    publishing = true;
    try {
      await publishFinding(repo.owner, repo.name, pr.number, finding);
      published = true;
      const msg = finding.priority === 'P1'
        ? 'Published as Request Changes review'
        : 'Published to PR';
      showToast(msg, 'success');
    } catch {
      showToast('Failed to publish', 'error');
    } finally {
      publishing = false;
    }
  }

  function handleAddToReview() {
    if (isStaged) {
      unstageFinding(finding);
      showToast('Removed from review', 'info');
    } else {
      stageFinding(finding);
      showToast('Added to review', 'success');
    }
  }

  function startEdit() {
    const currentKey = `${finding.priority}:${finding.title}:${finding.file ?? ''}:${finding.line ?? ''}`;
    editBody = $findingBodyOverrides.get(currentKey) ?? formatFindingBody(finding);
    editing = true;
  }

  function saveEdit() {
    if (editBody.trim()) {
      setFindingBodyOverride(finding, editBody.trim());
      showToast('Comment saved', 'info');
    }
    editing = false;
  }

  function cancelEdit() {
    editing = false;
    editBody = '';
  }

  function resetToAI() {
    clearFindingBodyOverride(finding);
    editing = false;
    editBody = '';
    showToast('Reset to AI-generated text', 'info');
  }
</script>

<div
  class="finding finding-{finding.priority.toLowerCase()}"
  class:staged={isStaged}
  style="animation-delay: {index * 60}ms"
>
  <div class="finding-header">
    <span class="badge badge-{finding.priority.toLowerCase()}">{finding.priority}</span>
    <span class="finding-title">{finding.title}</span>
    {#if finding.file}
      <span class="file-ref">{finding.file}{finding.line ? `:${finding.line}` : ''}</span>
    {/if}
    <div class="actions">
      {#if !published}
        <button
          class="btn btn-sm {editing ? 'btn-ghost' : hasOverride ? 'btn-edited' : 'btn-ghost'}"
          onclick={editing ? cancelEdit : startEdit}
          title={editing ? 'Cancel edit' : hasOverride ? 'Edit saved comment' : 'Edit comment before publishing'}
        >
          {editing ? '✗' : hasOverride ? '✎ Edited' : '✎'}
        </button>
      {/if}
      <button
        class="btn btn-sm {isStaged ? 'btn-staged' : 'btn-ghost'}"
        onclick={handleAddToReview}
        title={isStaged ? 'Remove from pending review' : 'Stage for batch review'}
      >
        {isStaged ? '✓ In review' : '+ Add to review'}
      </button>
      <button
        class="btn btn-sm {published ? 'btn-success' : finding.priority === 'P1' ? 'btn-warn' : 'btn-accent'}"
        onclick={handlePublish}
        disabled={publishing || published}
        title={finding.priority === 'P1' ? 'Publish as Request Changes review' : 'Publish as comment'}
      >
        {publishing ? '…' : published ? '✓ Published' : finding.priority === 'P1' ? '⚠ Request changes' : '↗ Publish'}
      </button>
    </div>
  </div>

  {#if editing}
    <div class="edit-mode">
      <label class="edit-label" for="edit-body-{index}">Edit comment body</label>
      <textarea
        id="edit-body-{index}"
        class="edit-textarea"
        rows="8"
        bind:value={editBody}
      ></textarea>
      <div class="edit-actions">
        <button class="btn btn-sm btn-ghost" onclick={cancelEdit}>✗ Discard</button>
        {#if hasOverride}
          <button class="btn btn-sm btn-ghost" onclick={resetToAI}>↺ Reset to AI</button>
        {/if}
        <button
          class="btn btn-sm btn-accent"
          onclick={saveEdit}
          disabled={!editBody.trim()}
        >💾 Save</button>
      </div>
    </div>
  {/if}

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
  .finding.staged {
    border-color: rgba(150, 110, 240, 0.55);
    background: rgba(120, 80, 220, 0.06);
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
  .actions { display: flex; align-items: center; gap: 6px; margin-left: auto; flex-shrink: 0; }
  .btn-ghost {
    background: transparent;
    border: 1px solid var(--glass-border);
    color: var(--text-secondary);
  }
  .btn-ghost:hover {
    border-color: rgba(150, 110, 240, 0.55);
    background: rgba(120, 80, 220, 0.08);
    color: var(--text-primary);
  }
  .btn-staged {
    background: rgba(120, 80, 220, 0.18);
    border: 1px solid rgba(150, 110, 240, 0.55);
    color: rgb(190, 160, 255);
  }
  .btn-warn {
    background: rgba(255, 140, 66, 0.16);
    border: 1px solid rgba(255, 140, 66, 0.45);
    color: rgb(255, 175, 110);
  }
  .btn-warn:hover:not(:disabled) {
    background: rgba(255, 140, 66, 0.25);
  }
  .btn-edited {
    background: rgba(80, 180, 120, 0.15);
    border: 1px solid rgba(80, 200, 120, 0.45);
    color: rgb(120, 220, 160);
  }
  .btn-edited:hover {
    background: rgba(80, 180, 120, 0.25);
  }
  .finding-desc { font-size: 12px; color: var(--text-secondary); line-height: 1.6; margin: 0; }
  .suggestion { margin-top: 10px; }
  .suggestion summary { font-size: 10px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.06em; cursor: pointer; user-select: none; }
  .suggestion summary:hover { color: var(--text-secondary); }
  .suggestion-body { margin-top: 8px; font-size: 11px; color: var(--text-secondary); background: rgba(0,0,0,0.25); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 10px 12px; white-space: pre-wrap; overflow-x: auto; line-height: 1.5; }
  .edit-mode {
    margin: 0 0 10px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .edit-label {
    font-size: 10px;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .edit-textarea {
    width: 100%;
    background: rgba(0, 0, 0, 0.25);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 11px;
    line-height: 1.6;
    padding: 10px 12px;
    resize: vertical;
    box-sizing: border-box;
    transition: border-color var(--transition-fast);
  }
  .edit-textarea:focus {
    outline: none;
    border-color: rgba(255, 107, 53, 0.55);
  }
  .edit-actions {
    display: flex;
    justify-content: flex-end;
    gap: 6px;
  }
</style>
