import type { SSEReviewEvent } from './types';

export type SSEHandler = (event: SSEReviewEvent) => void;
export type SSECleanup = () => void;

export function connectReviewStream(
  owner: string,
  repo: string,
  prNumber: number,
  onEvent: SSEHandler,
  rerun = false,
  model?: string,
): SSECleanup {
  const params = new URLSearchParams();
  if (rerun) params.set('rerun', 'true');
  if (model) params.set('model', model);
  const qs = params.toString();
  const url = `/api/review/${owner}/${repo}/${prNumber}/stream${qs ? `?${qs}` : ''}`;
  const source = new EventSource(url);

  let completed = false;

  source.onmessage = (e: MessageEvent) => {
    let parsed: SSEReviewEvent;
    try {
      parsed = JSON.parse(e.data) as SSEReviewEvent;
    } catch {
      return;
    }
    onEvent(parsed);
    if (parsed.type === 'done' || parsed.type === 'error') {
      completed = true;
      source.close();
    }
  };

  // Only treat onerror as a real error if the stream didn't complete normally.
  // EventSource fires onerror on normal server-side close, so we guard with the `completed` flag.
  source.onerror = () => {
    if (completed) return;
    onEvent({ type: 'error', message: 'Connection lost' });
    source.close();
  };

  return () => source.close();
}

// Parses SSE blocks from a raw chunk string. Assumes each event uses a single `data:` line.
export function parseSSEChunk(chunk: string): SSEReviewEvent[] {
  return chunk
    .split('\n\n')
    .filter((block) => block.startsWith('data:'))
    .map((block) => block.replace(/^data:\s*/, '').trim())
    .filter(Boolean)
    .flatMap((raw) => {
      try {
        return [JSON.parse(raw) as SSEReviewEvent];
      } catch {
        return [];
      }
    });
}
