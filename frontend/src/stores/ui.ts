import { writable } from 'svelte/store';
import type { Toast, ToastType } from '../lib/types';

export const toasts = writable<Toast[]>([]);

let toastId = 0;

export function showToast(message: string, type: ToastType = 'info'): void {
  const id = ++toastId;
  toasts.update((t) => [...t, { id, message, type }]);
  setTimeout(() => {
    toasts.update((t) => t.filter((x) => x.id !== id));
  }, 3000);
}
