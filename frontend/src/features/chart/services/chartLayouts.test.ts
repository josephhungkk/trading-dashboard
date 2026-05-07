import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { getChartLayout, putChartLayout } from './chartLayouts';
import type { ChartLayout } from './chartLayouts';

const SAMPLE_LAYOUT: ChartLayout = {
  payload: { indicators: ['MA'], chartType: 'candle' },
  schema_version: 1,
  updated_at: '2026-05-07T14:00:00Z',
};

describe('getChartLayout', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns null for 404', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 404 } as Response);

    const result = await getChartLayout(42);
    expect(result).toBeNull();
  });

  it('returns layout on success', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => SAMPLE_LAYOUT,
    } as Response);

    const result = await getChartLayout(42);
    expect(result).toEqual(SAMPLE_LAYOUT);
  });

  it('throws on non-404 error', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 500 } as Response);

    await expect(getChartLayout(42)).rejects.toThrow('layout fetch failed: 500');
  });

  it('calls correct URL with same-origin credentials', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => SAMPLE_LAYOUT,
    } as Response);

    await getChartLayout(99);

    expect(vi.mocked(fetch)).toHaveBeenCalledWith('/api/chart/layouts/99', {
      credentials: 'same-origin',
    });
  });
});

describe('putChartLayout', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('throws etag_mismatch on 412', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 412 } as Response);

    await expect(
      putChartLayout(42, { payload: {}, schema_version: 1 }, '2026-05-07T14:00:00Z'),
    ).rejects.toThrow('etag_mismatch');
  });

  it('throws generic error on other non-ok status', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 503 } as Response);

    await expect(
      putChartLayout(42, { payload: {}, schema_version: 1 }, '2026-05-07T14:00:00Z'),
    ).rejects.toThrow('layout put failed: 503');
  });

  it('returns updated layout on success', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => SAMPLE_LAYOUT,
    } as Response);

    const result = await putChartLayout(
      42,
      { payload: SAMPLE_LAYOUT.payload, schema_version: 1 },
      SAMPLE_LAYOUT.updated_at,
    );
    expect(result).toEqual(SAMPLE_LAYOUT);
  });

  it('sends If-Match header with quoted etag', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => SAMPLE_LAYOUT,
    } as Response);

    await putChartLayout(42, { payload: {}, schema_version: 1 }, '2026-05-07T14:00:00Z');

    const init = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)['If-Match']).toBe(
      '"2026-05-07T14:00:00Z"',
    );
  });
});
