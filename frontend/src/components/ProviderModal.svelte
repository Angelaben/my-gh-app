<script lang="ts">
  import {
    aiProvider,
    providersStatus,
    providerPickerOpen,
    selectedModel,
    showToast,
    type ProvidersStatus,
  } from '../stores/ui';

  let busy = $state(false);
  let errorMsg = $state('');

  async function pick(name: string): Promise<void> {
    if (busy) return;
    busy = true;
    errorMsg = '';
    try {
      const res = await fetch('/api/provider', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => res.statusText);
        errorMsg = `Failed to switch: ${text}`;
        return;
      }
      const status = (await res.json()) as ProvidersStatus & { warning?: string | null };
      providersStatus.set(status);
      aiProvider.set(status.active);
      // Switching providers invalidates the previously selected model.
      selectedModel.set('');
      providerPickerOpen.set(false);
      if (status.warning) {
        showToast(status.warning, 'error');
      } else {
        showToast(`AI provider: ${status.active}`, 'success');
      }
    } catch (e) {
      errorMsg = `Network error: ${e instanceof Error ? e.message : String(e)}`;
    } finally {
      busy = false;
    }
  }

  function close(): void {
    if (busy) return;
    providerPickerOpen.set(false);
  }

  function onKeyDown(e: KeyboardEvent): void {
    if (e.key === 'Escape') close();
  }

  // Falls back to the full provider list if the status hasn't loaded yet (so
  // the modal still works on the very first paint). Availability is assumed
  // until the real status from /api/providers replaces it a moment later.
  const FALLBACK: ProvidersStatus = {
    active: '',
    from_env: false,
    supported: ['opencode', 'claude-code'],
    available: { opencode: true, 'claude-code': true },
    clis: { opencode: 'opencode', 'claude-code': 'claude' },
  };

  let status = $derived<ProvidersStatus>($providersStatus ?? FALLBACK);
</script>

<svelte:window onkeydown={onKeyDown} />

{#if $providerPickerOpen}
  <div class="modal-backdrop" role="presentation">
    <!-- The backdrop button captures clicks outside the card to close.
         Keyboard accessibility: Escape is handled by the window listener;
         the modal traps focus on the close button. -->
    <button
      type="button"
      class="modal-backdrop-btn"
      onclick={close}
      aria-label="Close provider picker"
      tabindex="-1"
    ></button>
    <div
      class="modal-card"
      role="dialog"
      aria-modal="true"
      aria-labelledby="provider-modal-title"
    >
      <header class="modal-header">
        <h2 id="provider-modal-title">Select AI Provider</h2>
        <button class="close-btn" onclick={close} aria-label="Close" disabled={busy}>×</button>
      </header>

      <p class="modal-hint">
        Pick which CLI runs PR reviews and fixes. You can change this any time
        without restarting the server.
        {#if status.from_env}
          <br /><span class="env-note">
            <code>AI_PROVIDER</code> was set in the environment — switching from the UI
            overrides it for this session.
          </span>
        {/if}
      </p>

      <div class="provider-list">
        {#each status.supported as name}
          {@const cli = status.clis[name] ?? name}
          {@const installed = status.available[name] ?? false}
          {@const isActive = status.active === name}
          <button
            class="provider-card"
            class:active={isActive}
            class:claude={name === 'claude-code'}
            disabled={busy}
            onclick={() => pick(name)}
          >
            <div class="provider-row">
              <span class="provider-name">{name}</span>
              {#if isActive}<span class="badge active-badge">active</span>{/if}
            </div>
            <div class="provider-meta">
              <span class="cli-name">{cli}</span>
              {#if installed}
                <span class="badge ok-badge">installed</span>
              {:else}
                <span class="badge missing-badge">CLI not on PATH</span>
              {/if}
            </div>
          </button>
        {/each}
      </div>

      {#if errorMsg}
        <p class="modal-error">{errorMsg}</p>
      {/if}
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
    width: min(440px, 92vw);
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
  .env-note { color: var(--text-muted); font-size: 11px; }
  .provider-list { display: flex; flex-direction: column; gap: 8px; }
  .provider-card {
    text-align: left;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm);
    padding: 10px 12px;
    cursor: pointer;
    color: var(--text-primary);
    font-family: var(--font-mono);
    transition: border-color 120ms, background 120ms;
  }
  .provider-card:hover:not(:disabled) {
    border-color: rgba(255, 107, 53, 0.55);
    background: rgba(255, 107, 53, 0.06);
  }
  .provider-card.claude:hover:not(:disabled) {
    border-color: rgba(150, 110, 240, 0.55);
    background: rgba(120, 80, 220, 0.08);
  }
  .provider-card.active {
    border-color: rgba(255, 107, 53, 0.55);
    background: rgba(255, 107, 53, 0.06);
  }
  .provider-card.active.claude {
    border-color: rgba(150, 110, 240, 0.55);
    background: rgba(120, 80, 220, 0.08);
  }
  .provider-card:disabled { opacity: 0.5; cursor: not-allowed; }
  .provider-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
  }
  .provider-name {
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .provider-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 11px;
    color: var(--text-muted);
  }
  .cli-name {
    font-style: italic;
  }
  .badge {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 3px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 700;
  }
  .active-badge {
    background: rgba(255, 107, 53, 0.18);
    color: var(--accent);
  }
  .ok-badge {
    background: rgba(80, 200, 120, 0.18);
    color: rgb(120, 220, 150);
  }
  .missing-badge {
    background: rgba(220, 80, 80, 0.18);
    color: rgb(240, 130, 130);
  }
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
