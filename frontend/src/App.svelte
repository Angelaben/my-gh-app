<script lang="ts">
  import { onMount } from 'svelte';
  import { activeRepo, loadRepos } from './stores/repos';
  import { activePR, comments, activeTab } from './stores/prs';
  import { toasts, activeView } from './stores/ui';

  import Topbar from './components/Topbar.svelte';
  import Sidebar from './components/Sidebar.svelte';
  import EmptyState from './components/EmptyState.svelte';
  import PRList from './components/PRList.svelte';
  import PRDetail from './components/PRDetail.svelte';
  import ActivityPage from './components/ActivityPage.svelte';
  import ProviderModal from './components/ProviderModal.svelte';
  import Toast from './components/Toast.svelte';

  onMount(() => { loadRepos(); });

  $effect(() => {
    // Clear stale comment data whenever the active PR changes. Review state
    // lives in per-PR sessions (stores/reviewSessions.ts) and must survive
    // PR switches so background reviews keep running.
    $activePR;
    comments.set([]);
    activeTab.set('review');
  });
</script>

<div class="app">
  <Topbar />
  <Sidebar />
  <main class="main-content">
    {#if $activeView === 'activity'}
      <ActivityPage />
    {:else if !$activeRepo}
      <EmptyState />
    {:else if !$activePR}
      {#key $activeRepo.full_name}
        <PRList repo={$activeRepo} />
      {/key}
    {:else}
      <PRDetail pr={$activePR} repo={$activeRepo} />
    {/if}
  </main>
</div>

<ProviderModal />

{#each $toasts as toast (toast.id)}
  <Toast {toast} />
{/each}

<style>
  .app {
    height: 100vh;
    display: grid;
    grid-template-columns: 260px 1fr;
    grid-template-rows: 52px 1fr;
    grid-template-areas: 'topbar topbar' 'sidebar main';
    overflow: hidden;
  }
  .main-content {
    grid-area: main;
    overflow-y: auto;
    background: var(--bg-primary);
  }
</style>
