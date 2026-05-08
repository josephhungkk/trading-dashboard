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

// HIGH-5: use advanceTimersByTimeAsync — it advances timers AND drains all queued
// microtasks/promise callbacks per tick, preventing flaky test failures when the
// async chain in pushLayout → fetch().then() → handleResult spans multiple ticks.
async function advanceDebounce(ms = 500): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
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

  // CRIT-2: parent re-render during the debounce window must NOT reset the timer.
  it('parent re-render during pending debounce does not reset the timer', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ payload: {}, schema_version: 1, updated_at: 'etag-1' }),
    } as Response);

    const onConflict = vi.fn();
    const onError = vi.fn();
    const { rerender } = render(
      <ChartLayoutSync instrumentId={42} onConflict={onConflict} onError={onError} />,
    );

    act(() => {
      useChartStore.getState().setTimeframe('5m');
    });

    // Advance 300ms — still within debounce window
    await advanceDebounce(300);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();

    // Parent re-renders with new callback identity (simulates inline arrow recreation)
    rerender(
      <ChartLayoutSync instrumentId={42} onConflict={vi.fn()} onError={vi.fn()} />,
    );

    // Advance remaining 200ms — timer should fire exactly once, not be reset by rerender
    await advanceDebounce(200);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });

  // MED-2: switching instrumentId resets etag so new instrument uses If-Match: "".
  it('instrumentId change resets etag — new instrument PUT uses empty If-Match', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ payload: {}, schema_version: 1, updated_at: 'etag-1' }),
    } as Response);

    const { rerender } = render(<ChartLayoutSync instrumentId={42} />);

    // First PUT for instrument 42 — establishes etag-1
    act(() => {
      useChartStore.getState().setTimeframe('5m');
    });
    await advanceDebounce();
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);

    // Switch to instrument 99 — etag must reset to empty
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ payload: {}, schema_version: 1, updated_at: 'etag-2' }),
    } as Response);

    rerender(<ChartLayoutSync instrumentId={99} />);
    act(() => {
      useChartStore.getState().setTimeframe('15m');
    });
    await advanceDebounce();

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
    const secondInit = vi.mocked(fetch).mock.calls[1]?.[1] as RequestInit;
    expect((secondInit.headers as Record<string, string>)['If-Match']).toBe('""');
    expect(vi.mocked(fetch).mock.calls[1]?.[0]).toBe('/api/chart/layouts/99');
  });

  // HIGH-1: rapid serial PUTs each fire after debounce with their own generation;
  // a stale slow response from generation N-1 must not overwrite etag set by gen N.
  it('serial PUTs after rapid changes each carry fresh etag — no stale overwrite', async () => {
    // First PUT succeeds and establishes etag-1.
    // Second PUT succeeds and establishes etag-2.
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

    // Change 1 → debounce fires → PUT 1
    act(() => { useChartStore.getState().setTimeframe('5m'); });
    await advanceDebounce();

    // Change 2 → debounce fires → PUT 2 (must use etag-1 from PUT 1)
    act(() => { useChartStore.getState().setTimeframe('15m'); });
    await advanceDebounce();

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
    const secondInit = vi.mocked(fetch).mock.calls[1]?.[1] as RequestInit;
    expect((secondInit.headers as Record<string, string>)['If-Match']).toBe('"etag-1"');
  });
});
