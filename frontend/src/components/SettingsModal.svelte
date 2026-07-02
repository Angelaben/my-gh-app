<script lang="ts">
  import {
    settingsModalOpen,
    reviewSettings,
    prompts,
    saveSettings,
    resetSettings,
    savePrompt,
    resetPrompt,
  } from '../stores/settings';
  import { showToast } from '../stores/ui';
  import type { PromptName } from '../lib/types';

  let tab = $state<'review' | 'prompts'>('review');
  let busy = $state(false);

  // --- Review tab local form (synced from the store) ---
  let timeout = $state(300);
  let maxChars = $state(150000);
  let concurrency = $state(3);
  let globs = $state('');

  $effect(() => {
    const s = $reviewSettings;
    if (s) {
      timeout = s.review_timeout;
      maxChars = s.review_diff_max_chars;
      concurrency = s.review_max_concurrency;
      globs = s.review_ignore_globs.join(', ');
    }
  });

  // --- Prompts tab local form ---
  let promptName = $state<PromptName>('review');
  let promptDraft = $state('');

  $effect(() => {
    const p = $prompts;
    const name = promptName;
    if (p && p[name]) promptDraft = p[name].current;
  });

  const promptInfo = $derived($prompts?.[promptName] ?? null);
  const requiredPlaceholders = $derived(promptInfo?.required_placeholders ?? []);
  const missingPlaceholders = $derived.by(() => {
    if (!promptInfo) return [];
    const scan = promptDraft.replace(/\{\{/g, '').replace(/\}\}/g, '');
    return requiredPlaceholders.filter((ph) => !scan.includes(`{${ph}}`));
  });
  const promptDirty = $derived(!!promptInfo && promptDraft !== promptInfo.current);

  function errMsg(e: unknown, fallback: string): string {
    if (e instanceof Error) {
      try {
        const j = JSON.parse(e.message);
        if (j && typeof j.detail === 'string') return j.detail;
      } catch { /* not json */ }
      return e.message || fallback;
    }
    return fallback;
  }

  function splitGlobs(s: string): string[] {
    return s.split(',').map((x) => x.trim()).filter(Boolean);
  }

  async function onSaveReview(): Promise<void> {
    busy = true;
    try {
      await saveSettings({
        review_timeout: timeout,
        review_diff_max_chars: maxChars,
        review_max_concurrency: concurrency,
        review_ignore_globs: splitGlobs(globs),
      });
      showToast('Settings saved', 'success');
    } catch (e) {
      showToast(errMsg(e, 'Failed to save settings'), 'error');
    } finally {
      busy = false;
    }
  }

  async function onResetReview(): Promise<void> {
    busy = true;
    try {
      await resetSettings();
      showToast('Review settings restored to defaults', 'info');
    } catch (e) {
      showToast(errMsg(e, 'Failed to reset settings'), 'error');
    } finally {
      busy = false;
    }
  }

  async function onSavePrompt(): Promise<void> {
    if (missingPlaceholders.length > 0) {
      showToast(
        `Missing placeholder(s): ${missingPlaceholders.map((p) => `{${p}}`).join(', ')}`,
        'error',
      );
      return;
    }
    busy = true;
    try {
      await savePrompt(promptName, promptDraft);
      showToast(`Prompt "${promptName}" saved`, 'success');
    } catch (e) {
      showToast(errMsg(e, 'Failed to save prompt'), 'error');
    } finally {
      busy = false;
    }
  }

  async function onResetPrompt(): Promise<void> {
    busy = true;
    try {
      await resetPrompt(promptName);
      showToast(`Prompt "${promptName}" restored to default`, 'info');
    } catch (e) {
      showToast(errMsg(e, 'Failed to reset prompt'), 'error');
    } finally {
      busy = false;
    }
  }

  function close(): void {
    if (busy) return;
    settingsModalOpen.set(false);
  }

  function onKeyDown(e: KeyboardEvent): void {
    if (e.key === 'Escape') close();
  }
</script>

<svelte:window onkeydown={onKeyDown} />

{#if $settingsModalOpen}
  <div class="modal-backdrop" role="presentation">
    <button
      type="button"
      class="modal-backdrop-btn"
      onclick={close}
      aria-label="Close settings"
      tabindex="-1"
    ></button>
    <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <header class="modal-header">
        <h2 id="settings-title">Settings</h2>
        <button class="close-btn" onclick={close} aria-label="Close" disabled={busy}>×</button>
      </header>

      <div class="tabs">
        <button class="tab" class:active={tab === 'review'} onclick={() => (tab = 'review')}>⚙ Review</button>
        <button class="tab" class:active={tab === 'prompts'} onclick={() => (tab = 'prompts')}>✎ Prompts</button>
      </div>

      {#if tab === 'review'}
        <div class="body">
          <div class="field">
            <label for="set-timeout">Review timeout (s)</label>
            <input id="set-timeout" class="num" type="number" min="30" max="3600" bind:value={timeout} />
            <span class="hint">30–3600 · per-line inactivity</span>
          </div>
          <div class="field">
            <label for="set-maxchars">Max diff chars</label>
            <input id="set-maxchars" class="num" type="number" min="1000" bind:value={maxChars} />
            <span class="hint">above this → split &amp; merge</span>
          </div>
          <div class="field">
            <label for="set-conc">Max concurrency</label>
            <input id="set-conc" class="num" type="number" min="1" max="16" bind:value={concurrency} />
            <span class="hint">parallel chunk reviews</span>
          </div>
          <div class="field full">
            <label for="set-globs">Ignore globs</label>
            <input id="set-globs" class="txt" type="text" bind:value={globs} placeholder="*.lock, dist/*, *.min.js" />
          </div>
          <p class="note">
            ⓘ Empty = filtering disabled · stored in <code>.cache/settings.json</code> (overrides env defaults).
          </p>
          <div class="foot">
            <button class="btn btn-sm" onclick={onResetReview} disabled={busy}>Restore defaults</button>
            <button class="btn btn-sm btn-accent" onclick={onSaveReview} disabled={busy}>
              {busy ? '…' : 'Save'}
            </button>
          </div>
        </div>
      {:else}
        <div class="body">
          <div class="prompt-head">
            <select class="select" bind:value={promptName}>
              <option value="review">Review</option>
              <option value="fix">Fix</option>
              <option value="analyze">Analyze</option>
            </select>
            {#if promptInfo?.is_custom}<span class="modified">● Modified</span>{/if}
            {#if promptDirty}<span class="unsaved">unsaved changes</span>{/if}
            <button class="btn btn-sm" onclick={onResetPrompt} disabled={busy || !promptInfo?.is_custom}>
              ↺ Restore default
            </button>
          </div>
          <textarea class="ta" rows="12" bind:value={promptDraft} spellcheck="false"></textarea>
          <p class="note">
            ⓘ Required placeholders:
            {#each requiredPlaceholders as ph}<code>{`{${ph}}`}</code>{' '}{/each}
            · literal braces must be doubled (<code>{'{{'}</code> <code>{'}}'}</code>).
          </p>
          {#if missingPlaceholders.length > 0}
            <p class="warn">⚠ Missing: {missingPlaceholders.map((p) => `{${p}}`).join(', ')} — save is blocked.</p>
          {/if}
          <div class="foot">
            <button class="btn btn-sm" onclick={() => promptInfo && (promptDraft = promptInfo.current)} disabled={busy || !promptDirty}>Discard</button>
            <button class="btn btn-sm btn-accent" onclick={onSavePrompt} disabled={busy || missingPlaceholders.length > 0 || !promptDirty}>
              {busy ? '…' : 'Save prompt'}
            </button>
          </div>
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .modal-backdrop { position: fixed; inset: 0; z-index: 100; display: flex; align-items: center; justify-content: center; }
  .modal-backdrop-btn { position: absolute; inset: 0; background: rgba(0,0,0,0.55); backdrop-filter: blur(2px); border: none; padding: 0; cursor: pointer; }
  .modal-card {
    position: relative; width: min(580px, 94vw); max-height: 88vh; overflow-y: auto;
    background: rgba(20, 20, 26, 0.98); border: 1px solid var(--glass-border);
    border-radius: var(--radius-md); box-shadow: 0 24px 48px rgba(0,0,0,0.55);
    color: var(--text-primary); font-family: var(--font-mono);
  }
  .modal-header { display: flex; align-items: center; justify-content: space-between; padding: 16px 18px 0; }
  .modal-header h2 { margin: 0; font-size: 14px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--accent); }
  .close-btn { background: none; border: none; color: var(--text-muted); font-size: 22px; line-height: 1; cursor: pointer; padding: 0 4px; }
  .close-btn:hover { color: var(--text-primary); }
  .tabs { display: flex; gap: 4px; padding: 12px 18px 0; border-bottom: 1px solid var(--border); }
  .tab {
    padding: 7px 14px; border: 1px solid transparent; border-bottom: none;
    border-radius: var(--radius-sm) var(--radius-sm) 0 0; background: none;
    color: var(--text-secondary); font-family: var(--font-mono); font-size: 12px; cursor: pointer;
  }
  .tab:hover { color: var(--text-primary); }
  .tab.active { color: var(--accent); background: var(--glass-bg); border-color: var(--glass-border); border-bottom-color: transparent; margin-bottom: -1px; }
  .body { padding: 18px; display: flex; flex-direction: column; gap: 12px; }
  .field { display: grid; grid-template-columns: 160px 120px 1fr; align-items: center; gap: 12px; }
  .field.full { grid-template-columns: 160px 1fr; }
  .field label { color: var(--text-secondary); font-size: 12px; }
  .hint { color: var(--text-muted); font-size: 11px; }
  .num, .txt, .select, .ta {
    background: rgba(255,255,255,0.03); border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm); color: var(--text-primary);
    font-family: var(--font-mono); font-size: 12px; padding: 7px 9px; width: 100%;
  }
  .num:focus, .txt:focus, .ta:focus, .select:focus { outline: none; border-color: rgba(255,107,53,0.55); }
  .ta { resize: vertical; line-height: 1.55; white-space: pre; overflow-wrap: normal; overflow-x: auto; }
  .note { color: var(--text-muted); font-size: 11px; margin: 0; line-height: 1.5; }
  .note code { background: var(--bg-hover); padding: 1px 4px; border-radius: 3px; font-size: 10px; }
  .warn { color: var(--p1); font-size: 11px; margin: 0; }
  .prompt-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .prompt-head .select { width: auto; min-width: 120px; }
  .modified { color: var(--p1); font-size: 11px; }
  .unsaved { color: var(--text-muted); font-size: 11px; }
  .prompt-head .btn { margin-left: auto; }
  .foot { display: flex; gap: 8px; justify-content: flex-end; padding-top: 6px; border-top: 1px solid var(--border); margin-top: 2px; }
  .btn {
    padding: 7px 13px; border-radius: var(--radius-sm); border: 1px solid var(--border);
    background: var(--glass-bg); color: var(--text-primary); font-family: inherit; font-size: 12px; cursor: pointer;
  }
  .btn:hover:not(:disabled) { background: var(--glass-bg-hover); }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-sm { padding: 5px 11px; font-size: 11px; }
  .btn-accent { background: rgba(255,107,53,0.15); border-color: rgba(255,107,53,0.4); color: var(--accent); }
  .btn-accent:hover:not(:disabled) { background: rgba(255,107,53,0.25); }
</style>
