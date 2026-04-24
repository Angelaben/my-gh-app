import { writable } from 'svelte/store';
import type { Toast, ToastType } from '../lib/types';

export const toasts = writable<Toast[]>([]);

/** Which AI provider is active (fetched once from /api/config). */
export const aiProvider = writable<string>('');

const _LAST_MODEL_KEY = 'gh_review_last_model';

function createPersistedModel() {
  const initial = localStorage.getItem(_LAST_MODEL_KEY) ?? 'anthropic/claude-sonnet-4-5';
  const store = writable<string>(initial);
  store.subscribe((v) => localStorage.setItem(_LAST_MODEL_KEY, v));
  return store;
}

/** Selected OpenCode model. Persisted to localStorage across sessions. */
export const selectedModel = createPersistedModel();

/** AbortController for the currently running fix stream. Null when no fix is running. */
export const activeFixController = writable<AbortController | null>(null);

let toastId = 0;

export function showToast(message: string, type: ToastType = 'info'): void {
  const id = ++toastId;
  toasts.update((t) => [...t, { id, message, type }]);
  setTimeout(() => {
    toasts.update((t) => t.filter((x) => x.id !== id));
  }, 3000);
}
