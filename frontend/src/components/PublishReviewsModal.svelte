<script lang="ts">
  import type { Repo, PR } from '../lib/types';
  import {
    publishReviewsModalOpen,
    stagedFindingsByPR,
    publishStagedReview,
    unstageFinding,
  } from '../stores/prs';
  import { prKey } from '../stores/reviewSessions';
  import { showToast } from '../stores/ui';

  let { repo, pr }: { repo: Repo; pr: PR } = $props();

  const prk = $derived(prKey(repo.owner, repo.name, pr.number));
  const staged = $derived($stagedFindingsByPR.get(prk) ?? []);

  let summary = $state('');
  let busy = $state(false);
  let errorMsg = $state('');

  function close() {
    if (busy) return;
    publishReviewsModalOpen.set(false);
    errorMsg = '';
  }

  async function handlePublish() {
    if (busy) return;
    busy = true;
    errorMsg = '';
    try {
      const result = await publishStagedReview(repo.owner, repo.name, pr.number, summary);
      summary = '';
      publishReviewsModalOpen.set(false);
      if (result.warning) {
        showToast(result.warning, 'info');
      } else {
        showToast(`Review published as Request Changes`, 'success');
      }
    } catch (e) {
      errorMsg = `Failed to publish review: ${e instanceof Error ? e.message : String(e)}`;
    } finally {
      busy = false;
    }
  }

  function onKeyDown(e: KeyboardEvent): void {
    if (e.key === 'Escape') close();
  }
</script>

<svelte:window onkeydown={onKeyDown} />

{#if $publishReviewsModalOpen}
  <div class="modal-backdrop" role="presentation">
    <button
      type="button"
      class="modal-backdrop-btn"
      onclick={close}
      aria-label="Close publish reviews modal"
      tabindex="-1"
    ></button>
    <div
      class="modal-card"
      role="dialog"
      aria-modal="true"
      aria-labelledby="publish-reviews-title"
    >
      <header class="modal-header">
        <h2 id="publish-reviews-title">Publish Reviews</h2>
        <button class="close-btn" onclick={close} aria-label="Close" disabled={busy}>×</button>
      </header>

      <p class="modal-hint">
        Submits all staged findings as a single GitHub review with state
        <code>Request changes</code>. Findings with a file/line become inline
        comments; the rest go into the review body.
      </p>

      {#if staged.length === 0}
        <p class="empty">No findings staged. Click <strong>+ Add to review</strong> on findings to stage them.</p>
      {:else}
        <div class="staged-list">
          {#each staged as finding, i (i)}
            <div class="staged-item">
              <span class="badge badge-{finding.priority.toLowerCase()}">{finding.priority}</span>
              <div class="staged-body">
                <div class="staged-title">{finding.title}</div>
                {#if finding.file}
                  <div class="staged-file">{finding.file}{finding.line ? `:${finding.line}` : ''}</div>
                {:else}
                  <div class="staged-file no-file">no file/line — will appear in body</div>
                {/if}
              </div>
              <button
                class="btn btn-sm btn-ghost"
                onclick={() => unstageFinding(prk, finding)}
                disabled={busy}
                aria-label="Remove from review"
              >
                ×
              </button>
            </div>
          {/each}
        </div>

        <label class="summary-label" for="review-summary">
          Review summary (top-level review body)
        </label>
        <textarea
          id="review-summary"
          class="summary-input"
          rows="3"
          bind:value={summary}
          placeholder="e.g. Several issues need to be addressed before this can land."
          disabled={busy}
        ></textarea>
      {/if}

      {#if errorMsg}
        <p class="modal-error">{errorMsg}</p>
      {/if}

      <div class="modal-actions">
        <button class="btn btn-sm btn-ghost" onclick={close} disabled={busy}>Cancel</button>
        <button
          class="btn btn-sm btn-warn"
          onclick={handlePublish}
          disabled={busy || staged.length === 0}
        >
          {busy ? 'Publishing…' : `⚠ Publish as Request Changes (${staged.length})`}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 100;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .modal-backdrop-btn {
    position: absolute;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    backdrop-filter: blur(2px);
    border: none;
    padding: 0;
    cursor: pointer;
  }
  .modal-card {
    position: relative;
    width: min(560px, 92vw);
    max-height: 86vh;
    overflow-y: auto;
    background: rgba(20, 20, 26, 0.97);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-md);
    padding: 20px 22px 18px;
    box-shadow: 0 24px 48px rgba(0, 0, 0, 0.55);
    color: var(--text-primary);
    font-family: var(--font-mono);
  }
  .modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }
  .modal-header h2 {
    margin: 0;
    font-size: 14px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--accent);
  }
  .close-btn {
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 22px;
    line-height: 1;
    cursor: pointer;
    padding: 0 4px;
  }
  .close-btn:hover { color: var(--text-primary); }
  .modal-hint {
    font-size: 12px;
    color: var(--text-secondary);
    margin: 0 0 14px;
    line-height: 1.5;
  }
  .modal-hint code {
    background: rgba(255, 255, 255, 0.06);
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 11px;
  }
  .empty {
    padding: 24px;
    text-align: center;
    color: var(--text-muted);
    font-size: 12px;
    background: rgba(255, 255, 255, 0.02);
    border: 1px dashed var(--glass-border);
    border-radius: var(--radius-sm);
  }
  .staged-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 14px;
    max-height: 320px;
    overflow-y: auto;
  }
  .staged-item {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 8px 10px;
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm);
  }
  .staged-body { flex: 1; min-width: 0; }
  .staged-title { font-size: 12px; font-weight: 600; color: var(--text-primary); }
  .staged-file { font-size: 10px; color: var(--text-muted); font-style: italic; margin-top: 2px; }
  .staged-file.no-file { color: rgb(240, 130, 130); }
  .summary-label {
    display: block;
    font-size: 11px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 4px;
  }
  .summary-input {
    width: 100%;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm);
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 8px 10px;
    resize: vertical;
    box-sizing: border-box;
  }
  .summary-input:focus {
    outline: none;
    border-color: rgba(255, 107, 53, 0.55);
  }
  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 14px;
  }
  .btn-ghost {
    background: transparent;
    border: 1px solid var(--glass-border);
    color: var(--text-secondary);
  }
  .btn-ghost:hover:not(:disabled) {
    border-color: rgba(255, 107, 53, 0.55);
    color: var(--text-primary);
  }
  .btn-warn {
    background: rgba(255, 140, 66, 0.16);
    border: 1px solid rgba(255, 140, 66, 0.45);
    color: rgb(255, 175, 110);
  }
  .btn-warn:hover:not(:disabled) {
    background: rgba(255, 140, 66, 0.25);
  }
  .btn-warn:disabled { opacity: 0.5; cursor: not-allowed; }
  .modal-error {
    margin-top: 12px;
    padding: 8px 10px;
    background: rgba(220, 80, 80, 0.1);
    border: 1px solid rgba(220, 80, 80, 0.3);
    border-radius: var(--radius-sm);
    font-size: 12px;
    color: rgb(240, 130, 130);
  }
</style>
