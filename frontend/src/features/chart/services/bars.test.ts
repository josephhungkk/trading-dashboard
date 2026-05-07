import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fetchBars, toChartBars } from './bars';
import type { BarItem } from './bars';

const BASE_PARAMS = {
  canonicalId: 'AAPL.US',
  timeframe: '1m',
  start: new Date('2026-05-01T00:00:00Z'),
  end: new Date('2026-05-07T00:00:00Z'),
};

describe('fetchBars', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('builds correct URL with required params', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ bars: [], next_cursor: null }),
    } as Response);

    await fetchBars(BASE_PARAMS);

    const calledUrl = vi.mocked(fetch).mock.calls[0]?.[0] as string;
    expect(calledUrl).toContain('canonical_id=AAPL.US');
    expect(calledUrl).toContain('timeframe=1m');
    expect(calledUrl).toContain('start=');
    expect(calledUrl).toContain('end=');
  });

  it('appends cursor and limit when provided', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ bars: [], next_cursor: null }),
    } as Response);

    await fetchBars({ ...BASE_PARAMS, cursor: 'abc123', limit: 500 });

    const calledUrl = vi.mocked(fetch).mock.calls[0]?.[0] as string;
    expect(calledUrl).toContain('cursor=abc123');
    expect(calledUrl).toContain('limit=500');
  });

  it('throws on non-ok response', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      status: 503,
    } as Response);

    await expect(fetchBars(BASE_PARAMS)).rejects.toThrow('bars fetch failed: 503');
  });

  it('uses same-origin credentials', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ bars: [], next_cursor: null }),
    } as Response);

    await fetchBars(BASE_PARAMS);

    const calledInit = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    expect(calledInit.credentials).toBe('same-origin');
  });

  // HIGH-4: AbortSignal propagation
  it('passes AbortSignal to fetch when signal is provided', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ bars: [], next_cursor: null }),
    } as Response);

    const controller = new AbortController();
    await fetchBars({ ...BASE_PARAMS, signal: controller.signal });

    const calledInit = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    expect(calledInit.signal).toBe(controller.signal);
  });

  it('omits signal from fetch when not provided', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ bars: [], next_cursor: null }),
    } as Response);

    await fetchBars(BASE_PARAMS);

    const calledInit = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    // signal is undefined when not passed
    expect(calledInit.signal).toBeUndefined();
  });

  // MED-5: NaN / non-positive limit guard
  it('does not append limit param when limit is NaN', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ bars: [], next_cursor: null }),
    } as Response);

    await fetchBars({ ...BASE_PARAMS, limit: NaN });

    const calledUrl = vi.mocked(fetch).mock.calls[0]?.[0] as string;
    expect(calledUrl).not.toContain('limit=');
  });

  it('does not append limit param when limit is 0', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ bars: [], next_cursor: null }),
    } as Response);

    await fetchBars({ ...BASE_PARAMS, limit: 0 });

    const calledUrl = vi.mocked(fetch).mock.calls[0]?.[0] as string;
    expect(calledUrl).not.toContain('limit=');
  });

  it('does not append limit param when limit is negative', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ bars: [], next_cursor: null }),
    } as Response);

    await fetchBars({ ...BASE_PARAMS, limit: -100 });

    const calledUrl = vi.mocked(fetch).mock.calls[0]?.[0] as string;
    expect(calledUrl).not.toContain('limit=');
  });

  it('does not append limit param when limit is Infinity', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ bars: [], next_cursor: null }),
    } as Response);

    await fetchBars({ ...BASE_PARAMS, limit: Infinity });

    const calledUrl = vi.mocked(fetch).mock.calls[0]?.[0] as string;
    expect(calledUrl).not.toContain('limit=');
  });
});

describe('toChartBars', () => {
  const sampleBars: BarItem[] = [
    {
      bucket_start: '2026-05-07T14:00:00Z',
      open: '182.50',
      high: '183.10',
      low: '181.90',
      close: '182.75',
      volume: '42000',
      trade_count: 312,
    },
    {
      bucket_start: '2026-05-07T14:01:00Z',
      open: '182.75',
      high: '183.50',
      low: '182.60',
      close: '183.20',
      volume: '38500',
      trade_count: 280,
    },
  ];

  it('converts string OHLCV fields to numbers', () => {
    const result = toChartBars(sampleBars);
    expect(result[0]).toMatchObject({
      open: 182.5,
      high: 183.1,
      low: 181.9,
      close: 182.75,
      volume: 42000,
    });
  });

  it('converts bucket_start ISO string to ms timestamp', () => {
    const result = toChartBars(sampleBars);
    expect(result[0]?.timestamp).toBe(new Date('2026-05-07T14:00:00Z').getTime());
  });

  it('returns empty array for empty input', () => {
    expect(toChartBars([])).toEqual([]);
  });
});
