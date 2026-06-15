<script lang="ts">
  import { onMount } from 'svelte';
  import { activeRepo } from '../stores/repos';
  import { activePR, activeTab } from '../stores/prs';
  import { runningReviewCount } from '../stores/reviewSessions';
  import {
    activeView,
    aiProvider,
    providersStatus,
    providerPickerOpen,
    selectedModel,
    modelStorageKey,
    showToast,
    type ProvidersStatus,
  } from '../stores/ui';
  import { theme, THEMES } from '../stores/theme';
  import { openSettings } from '../stores/settings';

  const PROVIDER_PICKED_KEY = 'gh_review_provider_picked';

  let availableModels = $state<string[]>([]);
  let currentProvider = $state<string>('');

  async function loadProviders(): Promise<ProvidersStatus | null> {
    try {
      const res = await fetch('/api/providers');
      if (!res.ok) return null;
      const status = (await res.json()) as ProvidersStatus;
      providersStatus.set(status);
      currentProvider = status.active;
      aiProvider.set(currentProvider);
      return status;
    } catch {
      return null;
    }
  }

  async function loadModels(): Promise<void> {
    try {
      const res = await fetch('/api/models');
      if (!res.ok) {
        availableModels = [];
        return;
      }
      const data = await res.json();
      availableModels = data.models ?? [];
      if (data.warning) showToast(data.warning, 'error');
    } catch {
      availableModels = [];
    }
  }

  function applyStoredModel(): void {
    if (!currentProvider) {
      selectedModel.set('');
      return;
    }
    // Pick the active model from provider-scoped storage so a value cached
    // for opencode (e.g. "anthropic/claude-sonnet-4-6") never leaks into a
    // claude-code session and vice-versa. We never auto-pick
    // availableModels[0]: opencode's `models` listing can include identifiers
    // its runtime later rejects. "" sends no --model flag, so each provider
    // uses its own default.
    const stored = localStorage.getItem(modelStorageKey(currentProvider)) ?? '';
    const valid = stored !== '' && availableModels.includes(stored);
    selectedModel.set(valid ? stored : '');
  }

  onMount(async () => {
    const status = await loadProviders();
    await loadModels();
    applyStoredModel();

    if (!status) return;
    // Show the picker on first load when nothing is pre-decided: no env var,
    // no previous explicit choice, AND the active provider's CLI is missing.
    // (If the CLI is present, the user can keep using the auto-picked default
    // without being interrupted.)
    const hasPicked = localStorage.getItem(PROVIDER_PICKED_KEY) === '1';
    const activeAvailable = status.available[status.active] ?? false;
    if (!status.from_env && !hasPicked && !activeAvailable) {
      providerPickerOpen.set(true);
    }
    if (status.from_env && !activeAvailable) {
      showToast(
        `AI_PROVIDER=${status.active} but ${status.clis[status.active] ?? status.active} is not on PATH.`,
        'error',
      );
    } else if (status.active) {
      showToast(`AI provider: ${status.active}`, 'info');
    }
  });

  // When the provider changes (modal pick), refresh the model list and
  // re-apply provider-scoped model storage.
  $effect(() => {
    const next = $aiProvider;
    if (!next || next === currentProvider) return;
    currentProvider = next;
    localStorage.setItem(PROVIDER_PICKED_KEY, '1');
    void (async () => {
      await loadModels();
      applyStoredModel();
    })();
  });

  // The dropdown's reserved value used for "let me type a custom model id".
  const CUSTOM_SENTINEL = '__custom__';

  let customMode = $state(false);
  let customDraft = $state('');
  let customInput = $state<HTMLInputElement | null>(null);

  function persistModel(value: string): void {
    selectedModel.set(value);
    if (currentProvider) {
      localStorage.setItem(modelStorageKey(currentProvider), value);
    }
  }

  function onModelChange(value: string): void {
    if (value === CUSTOM_SENTINEL) {
      // Switch the row into the free-form input. Keep the existing custom
      // value (if any) as the draft so the user sees what they had.
      customDraft = $selectedModel || '';
      customMode = true;
      // Focus the input on the next tick after Svelte has rendered it.
      queueMicrotask(() => customInput?.focus());
      return;
    }
    customMode = false;
    persistModel(value);
  }

  function commitCustom(): void {
    const trimmed = customDraft.trim();
    customMode = false;
    persistModel(trimmed);
  }

  function cancelCustom(): void {
    customMode = false;
    // Don't change selectedModel — the dropdown's `value` binding will
    // re-sync against whatever was persisted.
  }

  // When the model is changed elsewhere (e.g. provider switch resets it to
  // ""), make sure we exit custom mode so the dropdown doesn't get stuck.
  $effect(() => {
    const m = $selectedModel;
    const isInList = availableModels.includes(m);
    if (m === '' || isInList) customMode = false;
  });

  function goHome(): void {
    activeView.set('workspace');
    activeRepo.set(null);
    activePR.set(null);
  }

  function goRepo(): void {
    activeView.set('workspace');
    activePR.set(null);
  }

  function toggleActivity(): void {
    activeView.update((v) => (v === 'activity' ? 'workspace' : 'activity'));
  }

  function openPicker(): void {
    providerPickerOpen.set(true);
  }

  let activeAvailable = $derived(
    !!$providersStatus && ($providersStatus.available[$providersStatus.active] ?? false),
  );
  let providerLabel = $derived($aiProvider || 'no provider');

  // Decide what the <select> should show. Three cases:
  //   1. No model picked → "<provider> default" (value="")
  //   2. Picked model is in availableModels → highlight it
  //   3. Picked model is custom (non-empty, not in list) OR user just opened
  //      the custom input → CUSTOM_SENTINEL highlights "Custom…"
  let dropdownValue = $derived(
    customMode || ($selectedModel !== '' && !availableModels.includes($selectedModel))
      ? CUSTOM_SENTINEL
      : $selectedModel,
  );
</script>

<header class="topbar">
  <button class="logo" onclick={goHome}>GH-REVIEW</button>
  <nav class="breadcrumb">
    {#if $activeRepo}
      <span class="sep">/</span>
      <button class="crumb" onclick={goRepo}>{$activeRepo.full_name}</button>
    {/if}
    {#if $activePR}
      <span class="sep">/</span>
      <button class="crumb active" onclick={() => activeTab.set('review')}>#{$activePR.number}</button>
    {/if}
  </nav>

  <div class="spacer"></div>

  {#if $runningReviewCount > 0}
    <span class="running-reviews" title="{$runningReviewCount} review(s) running in the background">
      <span class="running-spinner"></span>
      {$runningReviewCount} review{$runningReviewCount === 1 ? '' : 's'} running
    </span>
  {/if}

  <button
    type="button"
    class="activity-btn"
    class:active={$activeView === 'activity'}
    onclick={toggleActivity}
    title="Metrics, runs and live backend logs"
  >⊙ Activity</button>

  <button
    type="button"
    class="settings-btn"
    onclick={() => void openSettings()}
    title="Settings — review knobs &amp; AI prompts"
    aria-label="Settings"
  >⚙</button>

  <button
    type="button"
    class="provider-badge"
    class:provider-claude={$aiProvider === 'claude-code'}
    class:provider-missing={!activeAvailable && !!$aiProvider}
    onclick={openPicker}
    title={
      !$aiProvider
        ? 'Click to select an AI provider'
        : activeAvailable
        ? `Active AI provider: ${$aiProvider} (click to change)`
        : `${$aiProvider} CLI is not installed (click to switch)`
    }
  >
    {providerLabel}
    {#if !activeAvailable && $aiProvider}<span class="dot-warn" aria-hidden="true">!</span>{/if}
  </button>

  <div class="model-selector">
    <span class="model-label">Model</span>
    {#if customMode}
      <input
        bind:this={customInput}
        bind:value={customDraft}
        class="model-input"
        type="text"
        placeholder="model id, e.g. eu.anthropic.claude-sonnet-4-20250514-v1:0"
        onblur={commitCustom}
        onkeydown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); commitCustom(); }
          else if (e.key === 'Escape') { e.preventDefault(); cancelCustom(); }
        }}
      />
    {:else}
      <select
        class="model-select"
        value={dropdownValue}
        onchange={(e) => onModelChange((e.target as HTMLSelectElement).value)}
        disabled={!$aiProvider}
        title={$selectedModel || ($aiProvider ? `${$aiProvider} default` : 'no provider')}
      >
        <option value="">{$aiProvider ? `${$aiProvider} default` : 'no provider'}</option>
        {#each availableModels as m}
          <option value={m}>{m}</option>
        {/each}
        {#if $selectedModel !== '' && !availableModels.includes($selectedModel)}
          <option value={CUSTOM_SENTINEL}>{$selectedModel} (custom)</option>
        {:else}
          <option value={CUSTOM_SENTINEL}>Custom…</option>
        {/if}
      </select>
    {/if}
  </div>

  <div class="theme-picker">
    {#each THEMES as t}
      <button
        class="tp-btn"
        class:active={$theme === t.id}
        onclick={() => theme.set(t.id)}
        title={t.label}
        style="--dot: {t.accent}"
      >
        <span class="tp-dot"></span>{t.label}
      </button>
    {/each}
  </div>
</header>

<style>
  .topbar {
    grid-area: topbar;
    height: 52px;
    display: flex;
    align-items: center;
    gap: 0;
    padding: 0 20px;
    background: var(--topbar-bg);
    backdrop-filter: var(--glass-blur);
    border-bottom: 1px solid var(--glass-border);
    position: sticky;
    top: 0;
    z-index: 10;
  }
  .logo {
    background: none;
    border: none;
    color: var(--accent);
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 0.12em;
    cursor: pointer;
    padding: 4px 8px;
    border-radius: var(--radius-sm);
    transition: background var(--transition-fast), box-shadow var(--transition-fast);
  }
  .logo:hover { background: rgba(255,107,53,0.08); box-shadow: var(--glow-accent); }
  .breadcrumb { display: flex; align-items: center; gap: 2px; }
  .running-reviews {
    display: inline-flex; align-items: center; gap: 6px;
    margin-right: 10px; padding: 4px 10px;
    border: 1px solid color-mix(in srgb, var(--accent) 32%, transparent);
    border-radius: var(--radius-sm);
    background: color-mix(in srgb, var(--accent) 10%, transparent);
    color: var(--accent);
    font-family: var(--font-mono); font-size: 10px; font-weight: 700;
    letter-spacing: 0.04em; text-transform: uppercase;
    white-space: nowrap;
  }
  .running-spinner {
    width: 9px; height: 9px;
    border: 1.5px solid color-mix(in srgb, var(--accent) 30%, transparent);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    flex-shrink: 0;
  }
  .activity-btn {
    background: transparent;
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm);
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    padding: 5px 10px;
    cursor: pointer;
    margin-right: 10px;
    transition: all var(--transition-fast);
  }
  .activity-btn:hover { color: var(--text-secondary); border-color: var(--glass-border-hover); }
  .activity-btn.active {
    color: var(--accent);
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    border-color: color-mix(in srgb, var(--accent) 40%, transparent);
  }
  .settings-btn {
    background: transparent;
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm);
    color: var(--text-muted);
    font-size: 14px;
    line-height: 1;
    padding: 4px 9px;
    cursor: pointer;
    margin-right: 12px;
    transition: all var(--transition-fast);
  }
  .settings-btn:hover {
    color: var(--accent);
    border-color: color-mix(in srgb, var(--accent) 40%, transparent);
    background: color-mix(in srgb, var(--accent) 10%, transparent);
  }
  .sep { color: var(--text-muted); margin: 0 2px; font-size: 12px; }
  .crumb {
    background: none;
    border: none;
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 12px;
    cursor: pointer;
    padding: 3px 6px;
    border-radius: var(--radius-sm);
    transition: color var(--transition-fast), background var(--transition-fast);
  }
  .crumb:hover { color: var(--text-primary); background: var(--glass-bg-hover); }
  .crumb.active { color: var(--text-primary); }

  .spacer { flex: 1; }

  .provider-badge {
    margin-right: 12px;
    padding: 3px 8px;
    border-radius: var(--radius-sm);
    background: rgba(255, 107, 53, 0.12);
    border: 1px solid rgba(255, 107, 53, 0.35);
    color: var(--accent);
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 5px;
    transition: background var(--transition-fast), border-color var(--transition-fast);
  }
  .provider-badge:hover {
    background: rgba(255, 107, 53, 0.2);
    border-color: rgba(255, 107, 53, 0.6);
  }
  .provider-badge.provider-claude {
    background: rgba(120, 80, 220, 0.14);
    border-color: rgba(150, 110, 240, 0.45);
    color: rgb(190, 160, 250);
  }
  .provider-badge.provider-claude:hover {
    background: rgba(120, 80, 220, 0.22);
    border-color: rgba(150, 110, 240, 0.7);
  }
  .provider-badge.provider-missing {
    background: rgba(220, 80, 80, 0.14);
    border-color: rgba(220, 80, 80, 0.45);
    color: rgb(240, 130, 130);
  }
  .provider-badge.provider-missing:hover {
    background: rgba(220, 80, 80, 0.22);
    border-color: rgba(220, 80, 80, 0.7);
  }
  .dot-warn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: rgba(220, 80, 80, 0.45);
    color: white;
    font-size: 10px;
    font-weight: 800;
  }

  .model-selector {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .model-label {
    font-size: 10px;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .model-select {
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 3px 24px 3px 8px;
    cursor: pointer;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23888'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 7px center;
    transition: border-color var(--transition-fast), color var(--transition-fast);
  }
  .model-select:hover { border-color: rgba(255,107,53,0.4); color: var(--text-primary); }
  .model-select:focus { outline: none; border-color: var(--accent); }
  .model-input {
    background: var(--glass-bg);
    border: 1px solid var(--accent);
    border-radius: var(--radius-sm);
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 3px 8px;
    width: 360px;
    max-width: 50vw;
    outline: none;
  }
  .model-input::placeholder { color: var(--text-muted); }
  .model-input:focus { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(255,107,53,0.15); }

  .theme-picker {
    margin-left: 14px;
    display: flex;
    align-items: center;
    gap: 2px;
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    border-radius: 20px;
    padding: 3px 4px;
  }
  .tp-btn {
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 3px 9px;
    border-radius: 14px;
    border: none;
    background: none;
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    white-space: nowrap;
    transition: background var(--transition-fast), color var(--transition-fast);
  }
  .tp-btn:hover { color: var(--text-primary); background: var(--glass-bg-hover); }
  .tp-btn.active { color: var(--text-primary); background: var(--glass-bg-hover); }
  .tp-dot {
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--dot);
    flex-shrink: 0;
  }
</style>
