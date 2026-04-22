<script lang="ts">
  import { onMount } from 'svelte';
  import { activeRepo, loadRepos } from './stores/repos';
  import { activePR, cachedReview, comments, activeTab } from './stores/prs';
  import { toasts } from './stores/ui';

  import Topbar from './components/Topbar.svelte';
  import Sidebar from './components/Sidebar.svelte';
  import EmptyState from './components/EmptyState.svelte';
  import PRList from './components/PRList.svelte';
  import PRDetail from './components/PRDetail.svelte';
  import Toast from './components/Toast.svelte';

  onMount(() => { loadRepos(); });

  $effect(() => {
    // Clear stale review/comment data whenever the active PR changes.
    $activePR;
    cachedReview.set(null);
    comments.set([]);
    activeTab.set('review');
  });
</script>

<div class="app">
  <Topbar />
  <Sidebar />
  <main class="main-content">
    {#if !$activeRepo}
      <EmptyState />
    {:else if !$activePR}
      <PRList repo={$activeRepo} />
    {:else}
      <PRDetail pr={$activePR} repo={$activeRepo} />
    {/if}
  </main>
</div>

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
