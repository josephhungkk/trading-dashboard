import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { pushLayout, type LayoutPayload } from './layoutSync';
import type { ChartLayout } from './chartLayouts';

const SAMPLE_PAYLOAD: LayoutPayload = {
  timeframe: '1m',
  indicators: ['MA'],
  drawings: [],
  chartType: 'candle',
};

const REMOTE_LAYOUT: ChartLayout = {
  payload: SAMPLE_PAYLOAD,
  schema_version: 1,
  updated_at: 'etag-remote',
};

describe('pushLayout', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns ok with etag on 200', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ...REMOTE_LAYOUT, updated_at: 'etag-1' }),
    } as Response);

    const result = await pushLayout(42, SAMPLE_PAYLOAD, null);

    expect(result).toEqual({ kind: 'ok', etag: 'etag-1' });
  });

  it('returns conflict with remote layout on 412', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce({ ok: false, status: 412 } as Response)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => REMOTE_LAYOUT,
      } as Response);

    const result = await pushLayout(42, SAMPLE_PAYLOAD, 'etag-local');

    expect(result).toEqual({ kind: 'conflict', remote: REMOTE_LAYOUT });
    expect(vi.mocked(fetch)).toHaveBeenNthCalledWith(2, '/api/chart/layouts/42', {
      credentials: 'same-origin',
    });
  });

  it('returns layout_disappeared when 412 re-fetch returns 404', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce({ ok: false, status: 412 } as Response)
      .mockResolvedValueOnce({ ok: false, status: 404 } as Response);

    const result = await pushLayout(42, SAMPLE_PAYLOAD, 'etag-local');

    expect(result).toEqual({ kind: 'error', reason: 'layout_disappeared' });
  });

  it('returns error reason text on 500', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 500 } as Response);

    const result = await pushLayout(42, SAMPLE_PAYLOAD, 'etag-local');

    expect(result).toEqual({ kind: 'error', reason: 'layout put failed: 500' });
  });

  it('returns aborted when signal is already aborted', async () => {
    const controller = new AbortController();
    controller.abort();

    const result = await pushLayout(42, SAMPLE_PAYLOAD, 'etag-local', controller.signal);

    expect(result).toEqual({ kind: 'error', reason: 'aborted' });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it('passes expectedEtag as If-Match header', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => REMOTE_LAYOUT,
    } as Response);

    await pushLayout(42, SAMPLE_PAYLOAD, 'etag-local');

    const init = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)['If-Match']).toBe('"etag-local"');
  });
});
