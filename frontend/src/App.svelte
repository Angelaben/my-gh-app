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
  import LiveReviewPage from './components/LiveReviewPage.svelte';
  import { initLiveReview } from './stores/liveReview';
  import { loadSettings } from './stores/settings';
  import { initReviewStatus } from './stores/reviewStatus';
  import ProviderModal from './components/ProviderModal.svelte';
  import SettingsModal from './components/SettingsModal.svelte';
  import ReviewQueue from './components/ReviewQueue.svelte';
  import Toast from './components/Toast.svelte';

  const SIDEBAR_MIN = 180;
  const SIDEBAR_MAX = 480;
  const SIDEBAR_DEFAULT = 260;

  function loadSidebarWidth(): number {
    const v = Number(localStorage.getItem('sidebarWidth'));
    return v >= SIDEBAR_MIN && v <= SIDEBAR_MAX ? v : SIDEBAR_DEFAULT;
  }

  let sidebarWidth = $state(loadSidebarWidth());
  let dragging = $state(false);

  function clampWidth(w: number): number {
    return Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, w));
  }

  function onDrag(e: MouseEvent) {
    sidebarWidth = clampWidth(e.clientX);
  }

  function endDrag() {
    dragging = false;
    localStorage.setItem('sidebarWidth', String(sidebarWidth));
    window.removeEventListener('mousemove', onDrag);
    window.removeEventListener('mouseup', endDrag);
  }

  function startDrag(e: MouseEvent) {
    e.preventDefault();
    dragging = true;
    window.addEventListener('mousemove', onDrag);
    window.addEventListener('mouseup', endDrag);
  }

  function resetWidth() {
    sidebarWidth = SIDEBAR_DEFAULT;
    localStorage.setItem('sidebarWidth', String(sidebarWidth));
  }

  function onResizerKey(e: KeyboardEvent) {
    if (e.key === 'ArrowLeft') {
      sidebarWidth = clampWidth(sidebarWidth - 16);
    } else if (e.key === 'ArrowRight') {
      sidebarWidth = clampWidth(sidebarWidth + 16);
    } else {
      return;
    }
    e.preventDefault();
    localStorage.setItem('sidebarWidth', String(sidebarWidth));
  }

  // Live review feed connects at app level so auto-review toasts and the
  // topbar "Live" indicator work regardless of the active view.
  // Load settings up front so interval-driven features (open-PR auto-refresh,
  // needs-review badge) work without first opening the Settings modal.
  onMount(() => {
    loadRepos();
    void loadSettings().catch(() => {});
    void initLiveReview();
    initReviewStatus();
  });

  $effect(() => {
    // Clear stale comment data whenever the active PR changes. Review state
    // lives in per-PR sessions (stores/reviewSessions.ts) and must survive
    // PR switches so background reviews keep running.
    $activePR;
    comments.set([]);
    activeTab.set('review');
  });
</script>

<div class="app" class:dragging style="grid-template-columns: {sidebarWidth}px 1fr;">
  <Topbar />
  <Sidebar />
  <!-- ARIA window-splitter: a focusable separator is the standard resize pattern -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div
    class="sidebar-resizer"
    style="left: {sidebarWidth}px;"
    role="separator"
    aria-orientation="vertical"
    aria-label="Resize repositories panel"
    aria-valuenow={sidebarWidth}
    aria-valuemin={SIDEBAR_MIN}
    aria-valuemax={SIDEBAR_MAX}
    tabindex="0"
    onmousedown={startDrag}
    ondblclick={resetWidth}
    onkeydown={onResizerKey}
  ></div>
  <main class="main-content">
    {#if $activeView === 'activity'}
      <ActivityPage />
    {:else if $activeView === 'live-review'}
      <LiveReviewPage />
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
<SettingsModal />
<ReviewQueue />

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
    position: relative;
  }
  .app.dragging {
    cursor: col-resize;
    user-select: none;
  }
  .main-content {
    grid-area: main;
    overflow-y: auto;
    background: var(--bg-primary);
  }
  .sidebar-resizer {
    position: absolute;
    top: 52px;
    bottom: 0;
    width: 8px;
    transform: translateX(-50%);
    cursor: col-resize;
    z-index: 15;
    background: transparent;
    transition: background var(--transition-fast);
  }
  .sidebar-resizer::after {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    left: 50%;
    width: 1px;
    transform: translateX(-50%);
    background: transparent;
    transition: background var(--transition-fast);
  }
  .sidebar-resizer:hover::after,
  .sidebar-resizer:focus-visible::after,
  .app.dragging .sidebar-resizer::after {
    background: var(--accent);
  }
  .sidebar-resizer:focus-visible {
    outline: none;
  }
</style>
