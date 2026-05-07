import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { TradeChart } from './TradeChart';
import { useChartStore } from './stores/chartStore';
import { useLiveTailStore } from './stores/liveTailStore';

// vi.hoisted ensures these are available when vi.mock factory is hoisted to top of file.
const { mockSetDataLoader, mockSetSymbol, mockSetPeriod, mockCreateIndicator, mockDispose, mockInit } =
  vi.hoisted(() => {
    const mockSetDataLoader = vi.fn();
    const mockSetSymbol = vi.fn();
    const mockSetPeriod = vi.fn();
    const mockCreateIndicator = vi.fn();
    const mockDispose = vi.fn();
    const mockInit = vi.fn(() => ({
      setDataLoader: mockSetDataLoader,
      setSymbol: mockSetSymbol,
      setPeriod: mockSetPeriod,
      createIndicator: mockCreateIndicator,
    }));
    return { mockSetDataLoader, mockSetSymbol, mockSetPeriod, mockCreateIndicator, mockDispose, mockInit };
  });

// Mock klinecharts — canvas cannot render in jsdom.
// v10 API uses setDataLoader / setSymbol / setPeriod instead of applyNewData/updateData.
vi.mock('klinecharts', () => ({
  init: mockInit,
  dispose: mockDispose,
}));

// Minimal WebSocket mock
class MockWebSocket {
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn(() => {
    this.onclose?.();
  });
}

let mockWs: MockWebSocket;

function resetStores(): void {
  useChartStore.setState({
    timeframe: '1m',
    indicators: [],
    drawings: [],
    chartType: 'candle',
    activeDrawingTool: null,
  });
  useLiveTailStore.setState({
    lastSeen: new Map(),
    lockedBuckets: new Set(),
  });
}

describe('TradeChart', () => {
  beforeEach(() => {
    resetStores();
    mockInit.mockClear();
    mockSetDataLoader.mockClear();
    mockSetSymbol.mockClear();
    mockSetPeriod.mockClear();
    mockCreateIndicator.mockClear();
    mockDispose.mockClear();

    mockWs = new MockWebSocket();
    // Must use a real function (not arrow) so `new WebSocket(...)` works as a constructor.
    vi.stubGlobal('WebSocket', function MockWsConstructor() { return mockWs; });

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ bars: [], next_cursor: null }),
      }),
    );

    // Provide cf_authorization cookie so live-tail effect runs
    Object.defineProperty(document, 'cookie', {
      writable: true,
      configurable: true,
      value: 'cf_authorization=test-jwt',
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    Object.defineProperty(document, 'cookie', {
      writable: true,
      configurable: true,
      value: '',
    });
  });

  it('mounts canvas container with trade-chart testid', () => {
    render(<TradeChart canonicalId="AAPL.US" />);
    expect(screen.getByTestId('trade-chart')).toBeInTheDocument();
  });

  it('calls klinecharts init on mount', () => {
    render(<TradeChart canonicalId="AAPL.US" />);
    expect(mockInit).toHaveBeenCalledTimes(1);
  });

  it('calls fetchBars with correct canonicalId when getBars is invoked', async () => {
    // Capture DataLoader so we can trigger getBars manually.
    let capturedLoader: { getBars: (p: { callback: () => void }) => void } | null = null;
    mockSetDataLoader.mockImplementation((loader: typeof capturedLoader) => {
      capturedLoader = loader;
    });

    render(<TradeChart canonicalId="TSLA.US" />);

    expect(capturedLoader).not.toBeNull();
    const callback = vi.fn();
    // Explicit cast via unknown — TS can't narrow `let` across `await` in this shape.
    const loader = capturedLoader as unknown as { getBars: (p: { callback: () => void }) => void };
    await act(async () => {
      loader.getBars({ callback });
    });

    const calledUrl = vi.mocked(fetch).mock.calls[0]?.[0] as string;
    expect(calledUrl).toContain('canonical_id=TSLA.US');
  });

  it('sets DataLoader and calls getBars callback with converted bars', async () => {
    // Capture the DataLoader passed to setDataLoader so we can invoke getBars directly.
    let capturedLoader: Parameters<typeof mockSetDataLoader>[0] | null = null;
    mockSetDataLoader.mockImplementation((loader: Parameters<typeof mockSetDataLoader>[0]) => {
      capturedLoader = loader;
    });

    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        bars: [
          {
            bucket_start: '2026-05-07T14:00:00Z',
            open: '182.50',
            high: '183.10',
            low: '181.90',
            close: '182.75',
            volume: '42000',
            trade_count: 10,
          },
        ],
        next_cursor: null,
      }),
    } as Response);

    await act(async () => {
      render(<TradeChart canonicalId="AAPL.US" />);
    });

    expect(mockSetDataLoader).toHaveBeenCalledTimes(1);
    expect(capturedLoader).not.toBeNull();

    // Invoke getBars and verify the callback receives converted ChartBar data.
    const callback = vi.fn();
    await act(async () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (capturedLoader as any).getBars({ callback });
    });

    expect(callback).toHaveBeenCalledWith(
      expect.arrayContaining([expect.objectContaining({ open: 182.5, close: 182.75 })]),
      false,
    );
  });

  it('calls klinecharts dispose on unmount', () => {
    const { unmount } = render(<TradeChart canonicalId="AAPL.US" />);
    unmount();
    expect(mockDispose).toHaveBeenCalledTimes(1);
  });
});
