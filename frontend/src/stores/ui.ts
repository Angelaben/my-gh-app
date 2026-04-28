import { writable } from 'svelte/store';
import type { Toast, ToastType } from '../lib/types';

export const toasts = writable<Toast[]>([]);

/** Which AI provider is active (fetched once from /api/config). */
export const aiProvider = writable<string>('');

const _LAST_MODEL_KEY_PREFIX = 'gh_review_last_model';

export function modelStorageKey(provider: string): string {
  return `${_LAST_MODEL_KEY_PREFIX}:${provider}`;
}

/**
 * Selected AI model for the active provider. Empty string means "use the
 * provider's default" — the request omits the `model` param entirely.
 *
 * Initialised empty to avoid sending a stale value from a previous provider
 * (e.g. an `anthropic/...` opencode name to the claude-code CLI). The Topbar
 * populates this from provider-scoped localStorage once `/api/config` and
 * `/api/models` have resolved.
 */
export const selectedModel = writable<string>('');

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
