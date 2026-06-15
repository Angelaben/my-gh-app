import { writable } from 'svelte/store';
import { api } from '../lib/api';
import { showToast } from './ui';
import type { PromptInfo, PromptName, ReviewSettings } from '../lib/types';

/** Whether the unified Settings modal (review knobs + prompts) is open. */
export const settingsModalOpen = writable<boolean>(false);

export const reviewSettings = writable<ReviewSettings | null>(null);
export const reviewSettingsDefaults = writable<ReviewSettings | null>(null);
export const prompts = writable<Record<PromptName, PromptInfo> | null>(null);

export async function loadSettings(): Promise<void> {
  const data = await api.get<{ review: ReviewSettings; defaults: ReviewSettings }>('/settings');
  reviewSettings.set(data.review);
  reviewSettingsDefaults.set(data.defaults);
}

export async function saveSettings(partial: Partial<ReviewSettings>): Promise<void> {
  const data = await api.post<{ review: ReviewSettings }>('/settings', partial);
  reviewSettings.set(data.review);
}

export async function resetSettings(): Promise<void> {
  const data = await api.post<{ review: ReviewSettings }>('/settings/reset');
  reviewSettings.set(data.review);
}

export async function loadPrompts(): Promise<void> {
  prompts.set(await api.get<Record<PromptName, PromptInfo>>('/prompts'));
}

export async function savePrompt(name: PromptName, body: string): Promise<void> {
  prompts.set(await api.post<Record<PromptName, PromptInfo>>(`/prompts/${name}`, { body }));
}

export async function resetPrompt(name: PromptName): Promise<void> {
  prompts.set(await api.delete<Record<PromptName, PromptInfo>>(`/prompts/${name}`));
}

/** Open the modal and (re)load both settings and prompts. */
export async function openSettings(): Promise<void> {
  settingsModalOpen.set(true);
  try {
    await Promise.all([loadSettings(), loadPrompts()]);
  } catch {
    showToast('Failed to load settings', 'error');
  }
}
