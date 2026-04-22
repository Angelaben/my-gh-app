import { writable } from 'svelte/store';
import type { Repo } from '../lib/types';
import { api } from '../lib/api';

export const repos = writable<Repo[]>([]);
export const activeRepo = writable<Repo | null>(null);
export const repoSearchResults = writable<Repo[]>([]);

export async function loadRepos(): Promise<void> {
  const data = await api.get<Repo[]>('/repos');
  repos.set(data);
}

export async function addRepo(fullName: string): Promise<void> {
  const [owner, name] = fullName.split('/');
  const data = await api.post<Repo[]>('/repos', { owner, name });
  repos.set(data);
}

export async function removeRepo(fullName: string): Promise<void> {
  const data = await api.delete<Repo[]>('/repos', { full_name: fullName });
  repos.set(data);
  activeRepo.update((r) => (r?.full_name === fullName ? null : r));
}

export async function searchRepos(org: string, query: string): Promise<void> {
  if (query.length < 2) { repoSearchResults.set([]); return; }
  const data = await api.get<Repo[]>(`/repos/search?org=${encodeURIComponent(org)}&q=${encodeURIComponent(query)}`);
  repoSearchResults.set(data);
}
