<script lang="ts">
  import { onMount } from 'svelte';
  import { activeRepo } from '../stores/repos';
  import { activePR, activeTab } from '../stores/prs';
  import { aiProvider, selectedModel, modelStorageKey, showToast } from '../stores/ui';

  let availableModels = $state<string[]>([]);
  let currentProvider = $state<string>('');

  onMount(async () => {
    try {
      const [configRes, modelsRes] = await Promise.all([
        fetch('/api/config'),
        fetch('/api/models'),
      ]);
      if (configRes.ok) {
        const data = await configRes.json();
        currentProvider = data.ai_provider ?? '';
        aiProvider.set(currentProvider);
        if (currentProvider) showToast(`AI provider: ${currentProvider}`, 'info');
      }
      if (modelsRes.ok) {
        const data = await modelsRes.json();
        availableModels = data.models ?? [];
      }
      // Pick the active model from provider-scoped storage so a value cached
      // for opencode (e.g. "anthropic/claude-sonnet-4-6") never leaks into a
      // claude-code session and vice-versa.
      if (currentProvider) {
        const stored = localStorage.getItem(modelStorageKey(currentProvider)) ?? '';
        const valid = availableModels.includes(stored);
        const next = valid ? stored : (availableModels[0] ?? '');
        selectedModel.set(next);
      }
    } catch {
      // silently ignore
    }
  });

  function onModelChange(value: string) {
    selectedModel.set(value);
    if (currentProvider) {
      localStorage.setItem(modelStorageKey(currentProvider), value);
    }
  }

  function goHome() {
    activeRepo.set(null);
    activePR.set(null);
  }

  function goRepo() {
    activePR.set(null);
  }
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

  {#if $aiProvider}
    <span
      class="provider-badge"
      class:provider-claude={$aiProvider === 'claude-code'}
      title="Active AI provider (set via AI_PROVIDER env var)"
    >
      {$aiProvider}
    </span>
  {/if}

  {#if $aiProvider && availableModels.length > 0}
    <div class="model-selector">
      <span class="model-label">Model</span>
      <select
        class="model-select"
        value={$selectedModel}
        onchange={(e) => onModelChange((e.target as HTMLSelectElement).value)}
      >
        {#each availableModels as m}
          <option value={m}>{m}</option>
        {/each}
      </select>
    </div>
  {/if}
</header>

<style>
  .topbar {
    grid-area: topbar;
    height: 52px;
    display: flex;
    align-items: center;
    gap: 0;
    padding: 0 20px;
    background: rgba(10, 10, 15, 0.85);
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
  }
  .provider-badge.provider-claude {
    background: rgba(120, 80, 220, 0.14);
    border-color: rgba(150, 110, 240, 0.45);
    color: rgb(190, 160, 250);
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
</style>
