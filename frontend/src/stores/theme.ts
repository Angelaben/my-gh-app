import { writable } from 'svelte/store';

export type Theme = 'dark' | 'nord' | 'tokyo' | 'gruvbox';

export const THEMES: { id: Theme; label: string; accent: string }[] = [
  { id: 'dark',    label: 'Dark',    accent: '#ff6b35' },
  { id: 'nord',    label: 'Nord',    accent: '#88c0d0' },
  { id: 'tokyo',   label: 'Tokyo',   accent: '#7aa2f7' },
  { id: 'gruvbox', label: 'Gruvbox', accent: '#fabd2f' },
];

const STORAGE_KEY = 'gh-review-theme';

const initial: Theme =
  ((typeof localStorage !== 'undefined' ? localStorage.getItem(STORAGE_KEY) : null) as Theme | null)
  ?? 'dark';

// Apply before first render to avoid FOUC
if (typeof document !== 'undefined') {
  document.documentElement.setAttribute('data-theme', initial);
}

const { subscribe, set: _set } = writable<Theme>(initial);

export const theme = {
  subscribe,
  set(t: Theme) {
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem(STORAGE_KEY, t);
    _set(t);
  },
};
