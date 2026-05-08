import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { getChartLayout, putChartLayout, EtagMismatchError, resolveInstrumentId } from './chartLayouts';
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

    const [url, init] = vi.mocked(fetch).mock.calls[0] ?? [];
    expect(url).toBe('/api/chart/layouts/99');
    expect((init as RequestInit).credentials).toBe('same-origin');
  });

  // HIGH-3: signal is threaded into the GET request when provided.
  it('passes AbortSignal to fetch when provided (HIGH-3)', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => SAMPLE_LAYOUT,
    } as Response);

    const controller = new AbortController();
    await getChartLayout(42, controller.signal);

    const init = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    expect(init.signal).toBe(controller.signal);
  });

  it('does not set signal when not provided', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => SAMPLE_LAYOUT,
    } as Response);

    await getChartLayout(42);

    const init = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    expect(init.signal).toBeUndefined();
  });
});

describe('putChartLayout', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('throws EtagMismatchError on 412 (MED-4)', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 412 } as Response);

    await expect(
      putChartLayout(42, { payload: {}, schema_version: 1 }, '2026-05-07T14:00:00Z'),
    ).rejects.toBeInstanceOf(EtagMismatchError);
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

  // MED-6: control characters in etag must throw rather than be silently stripped.
  it('throws on etag containing control characters (MED-6)', async () => {
    await expect(
      putChartLayout(42, { payload: {}, schema_version: 1 }, 'bad\x00etag'),
    ).rejects.toThrow('invalid etag: contains control characters');
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it('allows empty etag (first-write path)', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => SAMPLE_LAYOUT,
    } as Response);

    await putChartLayout(42, { payload: {}, schema_version: 1 }, '');

    const init = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)['If-Match']).toBe('""');
  });

  // HIGH-4: signal must be passed as undefined (not null) to fetch.
  it('passes AbortSignal to fetch when provided (HIGH-4)', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => SAMPLE_LAYOUT,
    } as Response);

    const controller = new AbortController();
    await putChartLayout(42, { payload: {}, schema_version: 1 }, '', controller.signal);

    const init = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    expect(init.signal).toBe(controller.signal);
  });

  it('does not set signal property when signal not provided (HIGH-4)', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => SAMPLE_LAYOUT,
    } as Response);

    await putChartLayout(42, { payload: {}, schema_version: 1 }, '');

    const init = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    expect(init.signal).toBeUndefined();
  });
});

describe('resolveInstrumentId', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns instrument_id on success', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ instrument_id: 42 }),
    } as Response);

    const result = await resolveInstrumentId('stock:AAPL:US');
    expect(result).toBe(42);
  });

  it('returns null for 404 (instrument not seeded)', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 404 } as Response);

    const result = await resolveInstrumentId('stock:UNKNOWN:XX');
    expect(result).toBeNull();
  });

  it('throws on non-404 error', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 500 } as Response);

    await expect(resolveInstrumentId('stock:AAPL:US')).rejects.toThrow(
      'instrument resolve failed: 500',
    );
  });

  it('calls the resolve endpoint with URL-encoded canonical_id', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ instrument_id: 7 }),
    } as Response);

    await resolveInstrumentId('stock:AAPL:US');

    const [url] = vi.mocked(fetch).mock.calls[0] ?? [];
    expect(url).toBe('/api/chart/layouts/resolve?canonical_id=stock%3AAAPL%3AUS');
  });
});
