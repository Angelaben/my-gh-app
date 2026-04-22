<script lang="ts">
  import { activeRepo } from '../stores/repos';
  import { activePR, activeTab } from '../stores/prs';

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
</style>
