import { describe, it, expect, vi, beforeEach } from 'vitest';
import { api, ApiError } from './api';

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('api.get', () => {
  it('returns parsed JSON on 200', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ data: 'ok' }),
    }));
    const result = await api.get('/repos');
    expect(result).toEqual({ data: 'ok' });
    expect(fetch).toHaveBeenCalledWith('/api/repos', expect.objectContaining({ method: 'GET' }));
  });

  it('throws ApiError on non-ok response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      text: () => Promise.resolve('Not found'),
    }));
    await expect(api.get('/repos/missing')).rejects.toThrow(ApiError);
    await expect(api.get('/repos/missing')).rejects.toMatchObject({ status: 404 });
  });
});

describe('api.post', () => {
  it('sends JSON body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ created: true }),
    }));
    await api.post('/repos', { full_name: 'org/repo' });
    expect(fetch).toHaveBeenCalledWith('/api/repos', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ full_name: 'org/repo' }),
    }));
  });
});

describe('api.delete', () => {
  it('calls DELETE with correct path', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ deleted: true }),
    }));
    await api.delete('/repos/org%2Frepo');
    expect(fetch).toHaveBeenCalledWith('/api/repos/org%2Frepo', expect.objectContaining({ method: 'DELETE' }));
  });

  it('calls DELETE with a body when provided', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ deleted: true }),
    }));
    await api.delete('/repos', { full_name: 'org/repo' });
    expect(fetch).toHaveBeenCalledWith('/api/repos', expect.objectContaining({
      method: 'DELETE',
      body: JSON.stringify({ full_name: 'org/repo' }),
    }));
  });
});
