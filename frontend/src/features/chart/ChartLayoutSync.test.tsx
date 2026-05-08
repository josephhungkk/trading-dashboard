import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChartLayoutSync } from './ChartLayoutSync';
import type { ChartState } from './stores/chartStore';
import { useChartStore } from './stores/chartStore';

function resetChartStore(): void {
  useChartStore.setState({
    timeframe: '1m',
    indicators: [],
    drawings: [],
    chartType: 'candle',
    activeDrawingTool: null,
    pending_modify_id: new Map(),
  } satisfies Partial<ChartState>);
}

async function advanceDebounce(ms = 500): Promise<void> {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
  });
}

describe('ChartLayoutSync', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn());
    resetChartStore();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    resetChartStore();
  });

  it('does not PUT when instrumentId is null', async () => {
    render(<ChartLayoutSync instrumentId={null} />);

    act(() => {
      useChartStore.getState().setTimeframe('5m');
    });
    await advanceDebounce();

    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it('debounces a chartStore change for 500ms before PUT', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ payload: {}, schema_version: 1, updated_at: 'etag-1' }),
    } as Response);
    render(<ChartLayoutSync instrumentId={42} />);

    act(() => {
      useChartStore.getState().setTimeframe('5m');
    });
    await advanceDebounce(499);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();

    await advanceDebounce(1);

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe('/api/chart/layouts/42');
  });

  it('coalesces multiple rapid changes into one PUT', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ payload: {}, schema_version: 1, updated_at: 'etag-1' }),
    } as Response);
    render(<ChartLayoutSync instrumentId={42} />);

    act(() => {
      useChartStore.getState().setTimeframe('5m');
      useChartStore.getState().setChartType('area');
      useChartStore.getState().setIndicators(['RSI']);
    });
    await advanceDebounce();

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
    const init = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({
      payload: {
        timeframe: '5m',
        indicators: ['RSI'],
        drawings: [],
        chartType: 'area',
      },
      schema_version: 1,
    });
  });

  it('stores etag from 200 and uses it on the next PUT', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ payload: {}, schema_version: 1, updated_at: 'etag-1' }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ payload: {}, schema_version: 1, updated_at: 'etag-2' }),
      } as Response);
    render(<ChartLayoutSync instrumentId={42} />);

    act(() => {
      useChartStore.getState().setTimeframe('5m');
    });
    await advanceDebounce();
    act(() => {
      useChartStore.getState().setTimeframe('15m');
    });
    await advanceDebounce();

    const firstInit = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    const secondInit = vi.mocked(fetch).mock.calls[1]?.[1] as RequestInit;
    expect((firstInit.headers as Record<string, string>)['If-Match']).toBe('""');
    expect((secondInit.headers as Record<string, string>)['If-Match']).toBe('"etag-1"');
  });

  it('calls onConflict with remote layout on 412', async () => {
    const onConflict = vi.fn();
    const remote = {
      payload: { timeframe: '1d' },
      schema_version: 1,
      updated_at: 'etag-remote',
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce({ ok: false, status: 412 } as Response)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => remote,
      } as Response);
    render(<ChartLayoutSync instrumentId={42} onConflict={onConflict} />);

    act(() => {
      useChartStore.getState().setTimeframe('5m');
    });
    await advanceDebounce();

    expect(onConflict).toHaveBeenCalledWith(remote);
  });

  it('does not PUT when unmounted during pending debounce', async () => {
    const { unmount } = render(<ChartLayoutSync instrumentId={42} />);

    act(() => {
      useChartStore.getState().setTimeframe('5m');
    });
    unmount();
    await advanceDebounce();

    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});
