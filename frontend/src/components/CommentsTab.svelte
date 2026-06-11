<script lang="ts">
  import { onMount } from 'svelte';
  import { comments, loadComments, analyzeComments, newCommentIds } from '../stores/prs';
  import { showToast, activeFixController } from '../stores/ui';
  import { get } from 'svelte/store';
  import type { Repo, PR, Comment } from '../lib/types';
  import CommentCard from './CommentCard.svelte';

  let { repo, pr }: { repo: Repo; pr: PR } = $props();

  let loading = $state(true);
  let analyzing = $state(false);
  let analyzeController = $state<AbortController | null>(null);
  let filterAuthor = $state('');
  let filterPriority = $state('');
  let filterNew = $state(false);
  let filterNeedsReply = $state(false);

  onMount(async () => {
    try {
      await loadComments(repo.owner, repo.name, pr.number);
    } catch {
      showToast('Failed to load comments', 'error');
    } finally {
      loading = false;
    }
  });

  async function handleAnalyze() {
    const fixCtrl = get(activeFixController);
    if (fixCtrl) { fixCtrl.abort(); activeFixController.set(null); }

    const ctrl = new AbortController();
    analyzeController = ctrl;
    analyzing = true;
    try {
      await analyzeComments(repo.owner, repo.name, pr.number, ctrl.signal);
      showToast('Analysis complete', 'success');
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== 'AbortError') showToast('Analysis failed', 'error');
    } finally {
      analyzing = false;
      analyzeController = null;
    }
  }

  function stopAnalyze() {
    analyzeController?.abort();
  }

  const authors = $derived([...new Set($comments.map((c) => c.author))]);
  const hasAnalysis = $derived($comments.some((c) => c.analysis));
  const newIds = $derived(new Set($newCommentIds));

  function matchesCommentFilters(c: Comment): boolean {
    if (filterAuthor && c.author !== filterAuthor) return false;
    if (filterPriority && c.analysis?.priority !== filterPriority) return false;
    return true;
  }

  // Group comments into threads (root + replies). Thread order follows the
  // first occurrence of each thread; within a thread the root comes first,
  // replies in creation order.
  const threads = $derived.by(() => {
    const groups = new Map<number, Comment[]>();
    for (const c of $comments) {
      const key = c.thread_id ?? c.id;
      const group = groups.get(key);
      if (group) group.push(c);
      else groups.set(key, [c]);
    }
    return [...groups.entries()].map(([rootId, items]) => {
      items.sort((a, b) => {
        if (a.id === rootId) return -1;
        if (b.id === rootId) return 1;
        return (a.created_at ?? '').localeCompare(b.created_at ?? '');
      });
      return { rootId, items };
    });
  });

  // A thread is shown when at least one of its comments passes the filters;
  // the whole thread is then rendered so replies keep their context.
  const visibleThreads = $derived(
    threads.filter(({ items }) => {
      if (!items.some(matchesCommentFilters)) return false;
      if (filterNew && !items.some((c) => newIds.has(c.id) || c.is_new_reply)) return false;
      if (filterNeedsReply) {
        const last = items[items.length - 1];
        if (last.is_ours) return false;
        // Only threads we participate in count as "awaiting our reply".
        if (!items.some((c) => c.is_ours)) return false;
      }
      return true;
    })
  );

  const visibleCount = $derived(visibleThreads.reduce((n, t) => n + t.items.length, 0));
</script>

<div class="comments-tab">
  <div class="toolbar">
    {#if analyzing}
      <button class="btn btn-danger btn-sm" onclick={stopAnalyze}>⏹ Stop</button>
      <span class="analyzing-label">Analyzing…</span>
    {:else}
      <button class="btn btn-accent" onclick={handleAnalyze}>⚙ Analyze Comments</button>
    {/if}
    <select class="filter-select" bind:value={filterAuthor}>
      <option value="">All authors</option>
      {#each authors as author}
        <option value={author}>{author}</option>
      {/each}
    </select>
    <select class="filter-select" bind:value={filterPriority} disabled={!hasAnalysis}>
      <option value="">All severities</option>
      <option value="P0">P0</option>
      <option value="P1">P1</option>
      <option value="P2">P2</option>
      <option value="P3">P3</option>
    </select>
    <button
      class="filter-chip"
      class:active={filterNew}
      disabled={$newCommentIds.length === 0}
      onclick={() => filterNew = !filterNew}
      title="Threads with comments posted since your last visit"
    >🆕 New{#if $newCommentIds.length > 0}&nbsp;({$newCommentIds.length}){/if}</button>
    <button
      class="filter-chip"
      class:active={filterNeedsReply}
      onclick={() => filterNeedsReply = !filterNeedsReply}
      title="Threads you participate in where someone else has the last word"
    >↩ Needs reply</button>
    <span class="count">{visibleCount} / {$comments.length}</span>
  </div>

  {#if loading}
    <div class="loading-state">
      <div class="spinner"></div>
      <span style="color:var(--text-muted)">Loading comments…</span>
    </div>
  {:else if visibleThreads.length === 0}
    <div class="empty-state-inline">No comments match filters.</div>
  {:else}
    {#each visibleThreads as { rootId, items } (rootId)}
      <div class="thread" class:has-replies={items.length > 1}>
        {#each items as comment, idx (comment.id)}
          <div class="thread-item" class:thread-reply={idx > 0}>
            <CommentCard {comment} {repo} {pr} isNew={newIds.has(comment.id) || comment.is_new_reply === true} />
          </div>
        {/each}
      </div>
    {/each}
  {/if}
</div>

<style>
  .comments-tab { display: flex; flex-direction: column; gap: 10px; }

  .toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .filter-select {
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 5px 8px;
    cursor: pointer;
    transition: border-color var(--transition-fast);
  }
  .filter-select:focus { outline: none; border-color: var(--border-active); }
  .filter-select:disabled { opacity: 0.4; cursor: default; }

  .filter-chip {
    padding: 4px 10px;
    border-radius: 14px;
    border: 1px solid var(--glass-border);
    background: transparent;
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 10px;
    cursor: pointer;
    transition: all var(--transition-fast);
  }
  .filter-chip:hover:not(:disabled) { border-color: var(--glass-border-hover); color: var(--text-secondary); }
  .filter-chip.active {
    background: color-mix(in srgb, var(--accent) 14%, transparent);
    border-color: color-mix(in srgb, var(--accent) 40%, transparent);
    color: var(--accent);
  }
  .filter-chip:disabled { opacity: 0.4; cursor: default; }

  .thread { display: flex; flex-direction: column; }
  .thread.has-replies {
    border-left: 2px solid var(--glass-border);
    padding-left: 0;
  }
  .thread-reply { margin-left: 26px; position: relative; }
  .thread-reply::before {
    content: '↳';
    position: absolute; left: -16px; top: 14px;
    color: var(--text-muted); font-size: 11px;
  }

  .count { font-size: 10px; color: var(--text-muted); margin-left: auto; }
  .analyzing-label { font-size: 11px; color: var(--text-muted); font-style: italic; }

  .loading-state {
    display: flex; align-items: center; gap: 10px; padding: 40px 0; justify-content: center;
  }
  .empty-state-inline { color: var(--text-muted); padding: 40px 0; text-align: center; font-size: 12px; }
</style>
