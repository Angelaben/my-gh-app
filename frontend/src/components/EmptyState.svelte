<script lang="ts"></script>

<div class="empty">
  <div class="icon">&lt;/&gt;</div>
  <p>Select a repository from the sidebar or add one to get started.</p>
</div>

<style>
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    gap: 1rem;
    color: var(--text-muted);
    animation: fadeIn 0.3s ease;
  }
  .icon { font-size: 2.5rem; color: var(--border-active); font-family: var(--font-mono); }
  p { font-size: 13px; max-width: 340px; text-align: center; line-height: 1.6; }
</style>
