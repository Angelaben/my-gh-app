import { describe, it, expect } from 'vitest';
import { parseSSEChunk } from './sse';

describe('parseSSEChunk', () => {
  it('parses a single chunk event', () => {
    const input = 'data: {"type":"chunk","text":"hello"}\n\n';
    expect(parseSSEChunk(input)).toEqual([{ type: 'chunk', text: 'hello' }]);
  });

  it('parses a result event', () => {
    const findings = [{ priority: 'P0', title: 'SQL injection', description: 'desc' }];
    const input = `data: ${JSON.stringify({ type: 'result', review: { findings } })}\n\n`;
    const result = parseSSEChunk(input);
    expect(result[0]).toMatchObject({ type: 'result', review: { findings } });
  });

  it('parses multiple events in one chunk', () => {
    const input = 'data: {"type":"chunk","text":"a"}\n\ndata: {"type":"done"}\n\n';
    expect(parseSSEChunk(input)).toHaveLength(2);
    expect(parseSSEChunk(input)[1]).toEqual({ type: 'done' });
  });

  it('ignores malformed JSON', () => {
    const input = 'data: not-json\n\n';
    expect(parseSSEChunk(input)).toHaveLength(0);
  });

  it('ignores lines without data: prefix', () => {
    const input = 'comment: ignore me\n\ndata: {"type":"done"}\n\n';
    expect(parseSSEChunk(input)).toHaveLength(1);
  });
});
