// frontend/src/services/algo/api.test.ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getAlgoCapabilities, getAlgoSchemas } from './api';

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
}

function stubFetch(response: Response): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(() => Promise.resolve(response));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('getAlgoCapabilities', () => {
  it('returns strategies for ibkr/STOCK', async () => {
    const payload = {
      strategies: [
        { strategy: 'TWAP', params: [{ name: 'start_time', type: 'time', required: true }] },
      ],
    };
    const fetchMock = stubFetch(jsonResponse(payload));
    const result = await getAlgoCapabilities('ibkr', 'STOCK');
    expect(result.strategies).toHaveLength(1);
    expect(result.strategies[0]?.strategy).toBe('TWAP');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/algo/capabilities/ibkr/STOCK',
      { credentials: 'include' },
    );
  });

  it('returns empty strategies for schwab', async () => {
    stubFetch(jsonResponse({ strategies: [] }));
    const result = await getAlgoCapabilities('schwab', 'STOCK');
    expect(result.strategies).toHaveLength(0);
  });

  it('throws on non-ok response', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('err', { status: 503 }))));
    await expect(getAlgoCapabilities('ibkr', 'STOCK')).rejects.toThrow('503');
  });
});

describe('getAlgoSchemas', () => {
  it('returns schemas dict', async () => {
    stubFetch(
      jsonResponse({
        schemas: { ADAPTIVE: [{ name: 'urgency', type: 'enum', required: true }] },
      }),
    );
    const result = await getAlgoSchemas();
    expect(result.schemas['ADAPTIVE']).toBeDefined();
  });
});
