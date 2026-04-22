<script lang="ts">
  import { repos, activeRepo, repoSearchResults, addRepo, removeRepo, searchRepos } from '../stores/repos';
  import { activePR } from '../stores/prs';
  import { showToast } from '../stores/ui';
  import type { Repo } from '../lib/types';

  let searchQuery = $state('');
  let showResults = $state(false);

  async function handleSearch() {
    if (searchQuery.includes('/')) {
      await handleAdd(searchQuery);
    } else if (searchQuery.length >= 2) {
      // Use org from first known repo, or skip if unknown
      const org = $repos[0]?.owner;
      if (!org) {
        showToast('Add a repo first to enable search', 'info');
        return;
      }
      await searchRepos(org, searchQuery);
      showResults = true;
    }
  }

  async function handleAdd(fullName: string) {
    const trimmed = fullName.trim();
    if (!/^[^/]+\/[^/]+$/.test(trimmed)) {
      showToast('Use format: owner/repo', 'error');
      return;
    }
    try {
      await addRepo(trimmed);
      searchQuery = '';
      showResults = false;
      showToast(`Added ${trimmed}`, 'success');
    } catch {
      showToast('Failed to add repo', 'error');
    }
  }

  async function handleSelect(repo: Repo) {
    activeRepo.set(repo);
    activePR.set(null);
    showResults = false;
  }

  async function handleRemove(e: MouseEvent, fullName: string) {
    e.stopPropagation();
    try {
      await removeRepo(fullName);
    } catch {
      showToast('Failed to remove repo', 'error');
    }
  }
</script>

<aside class="sidebar">
  <div class="sidebar-header">
    <span class="sidebar-title">Repositories</span>
    <div class="add-form">
      <input
        class="search-input"
        type="text"
        placeholder="org/repo or search…"
        bind:value={searchQuery}
        onkeydown={(e) => e.key === 'Enter' && handleSearch()}
        onfocus={() => { if (searchQuery.length >= 2) showResults = true; }}
        onblur={() => setTimeout(() => { showResults = false; }, 150)}
      />
      <button class="btn btn-accent btn-sm" onclick={handleSearch}>+</button>
    </div>
    {#if showResults && $repoSearchResults.length > 0}
      <div class="search-results">
        {#each $repoSearchResults as r (r.full_name)}
          <button class="result-item" onmousedown={() => handleAdd(r.full_name)}>
            {r.full_name}
          </button>
        {/each}
      </div>
    {/if}
  </div>

  <div class="repo-list">
    {#each $repos as repo (repo.full_name)}
      <div
        class="repo-item"
        class:active={$activeRepo?.full_name === repo.full_name}
        role="button"
        tabindex="0"
        onclick={() => handleSelect(repo)}
        onkeydown={(e) => e.key === 'Enter' && handleSelect(repo)}
      >
        <span class="repo-name">{repo.full_name}</span>
        <button class="remove-btn" onclick={(e) => handleRemove(e, repo.full_name)} aria-label="Remove">×</button>
      </div>
    {/each}
  </div>
</aside>

<style>
  .sidebar {
    grid-area: sidebar;
    background: rgba(18,18,26,0.7);
    backdrop-filter: var(--glass-blur);
    border-right: 1px solid var(--glass-border);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .sidebar-header {
    padding: 14px 12px 10px;
    border-bottom: 1px solid var(--border);
    position: relative;
  }
  .sidebar-title {
    display: block;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-bottom: 10px;
  }
  .add-form { display: flex; gap: 6px; }
  .search-input {
    flex: 1;
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm);
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 5px 8px;
    transition: border-color var(--transition-fast);
    min-width: 0;
  }
  .search-input:focus { outline: none; border-color: var(--border-active); background: var(--glass-bg-hover); }
  .search-input::placeholder { color: var(--text-muted); }
  .search-results {
    position: absolute;
    top: calc(100% - 4px);
    left: 12px; right: 12px;
    background: var(--bg-secondary);
    border: 1px solid var(--border-active);
    border-radius: var(--radius-sm);
    max-height: 180px;
    overflow-y: auto;
    z-index: 20;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  }
  .result-item {
    display: block; width: 100%; text-align: left;
    background: none; border: none;
    color: var(--text-secondary);
    font-family: var(--font-mono); font-size: 11px;
    padding: 7px 10px; cursor: pointer;
    transition: background var(--transition-fast), color var(--transition-fast);
  }
  .result-item:hover { background: var(--glass-bg-hover); color: var(--text-primary); }
  .repo-list { flex: 1; overflow-y: auto; padding: 6px; }
  .repo-item {
    display: flex; align-items: center; width: 100%;
    background: none; border: 1px solid transparent;
    border-radius: var(--radius-sm);
    color: var(--text-secondary);
    font-family: var(--font-mono); font-size: 11px;
    padding: 7px 8px; cursor: pointer; text-align: left;
    transition: background var(--transition-fast), border-color var(--transition-fast), color var(--transition-fast);
    margin-bottom: 2px;
  }
  .repo-item:hover { background: var(--glass-bg); border-color: var(--glass-border); color: var(--text-primary); }
  .repo-item.active { background: rgba(255,107,53,0.08); border-color: rgba(255,107,53,0.25); color: var(--accent); }
  .repo-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .remove-btn {
    background: none; border: none; color: var(--text-muted);
    font-size: 14px; cursor: pointer; padding: 0 2px; line-height: 1;
    opacity: 0; transition: color var(--transition-fast), opacity var(--transition-fast); flex-shrink: 0;
  }
  .repo-item:hover .remove-btn { opacity: 1; }
  .remove-btn:hover { color: var(--p0); }
</style>
