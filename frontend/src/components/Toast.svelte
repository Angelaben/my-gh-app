<script lang="ts">
  import type { Toast } from '../lib/types';
  let { toast }: { toast: Toast } = $props();
</script>

<div class="toast toast-{toast.type}" role="alert">
  {toast.message}
</div>

<style>
  .toast {
    position: fixed; bottom: 20px; right: 20px;
    padding: 10px 16px; border-radius: var(--radius-md);
    font-size: 12px; font-weight: 500;
    backdrop-filter: var(--glass-blur);
    border: 1px solid var(--glass-border);
    animation: slideUp 0.2s ease both;
    z-index: 100; max-width: 320px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
    pointer-events: none;
  }
  .toast-success { background: rgba(78,205,196,0.15); border-color: rgba(78,205,196,0.3); color: var(--success); }
  .toast-error { background: rgba(255,59,59,0.15); border-color: rgba(255,59,59,0.3); color: var(--p0); }
  .toast-info { background: rgba(255,107,53,0.1); border-color: rgba(255,107,53,0.25); color: var(--accent); }
</style>
