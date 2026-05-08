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
  registerOverlay: vi.fn(),
}));

// WebSocket mock that records every instantiated instance and exposes close code support.
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  protocols: string | string[];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn((code = 1000) => {
    this.onclose?.({ code });
  });

  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = protocols ?? [];
    MockWebSocket.instances.push(this);
  }
}

function resetStores(): void {
  useChartStore.setState({
    timeframe: '1m',
    indicators: [],
    drawings: [],
    chartType: 'candle',
    activeDrawingTool: null,
  });
  // MED-2: liveTailStore now uses nested Maps for both lastSeen and lockedBuckets.
  useLiveTailStore.setState({
    lastSeen: new Map(),
    lockedBuckets: new Map(),
  });
}

describe('TradeChart', () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    resetStores();
    mockInit.mockClear();
    mockSetDataLoader.mockClear();
    mockSetSymbol.mockClear();
    mockSetPeriod.mockClear();
    mockCreateIndicator.mockClear();
    mockDispose.mockClear();

    // Must use a class constructor — can't use arrow function for `new WebSocket(...)`.
    vi.stubGlobal('WebSocket', MockWebSocket);

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

  // HIGH-1: stable selector test — no spurious WS reconnect on store action calls.
  // If the whole liveTail store object were subscribed instead of individual selectors,
  // every recordSeen call (on each WS tick) would produce a new store reference,
  // causing useEffect to re-run and tear down + reconnect the WS.
  it('HIGH-1: does not create extra WS connections when liveTail actions fire', async () => {
    vi.useFakeTimers();
    await act(async () => {
      render(<TradeChart canonicalId="AAPL.US" />);
    });

    // One WS should have been opened for the live tail.
    expect(MockWebSocket.instances).toHaveLength(1);

    // Simulate store action calls (recordSeen / lockBucket) that mutate the store.
    // If TradeChart subscribed to the whole store these would re-trigger useEffect.
    await act(async () => {
      useLiveTailStore.getState().recordSeen('AAPL.US', '1m', '2026-05-07T14:00:00Z', 1);
      useLiveTailStore.getState().recordSeen('AAPL.US', '1m', '2026-05-07T14:00:00Z', 2);
      useLiveTailStore.getState().lockBucket('AAPL.US', '1m', '2026-05-07T14:00:00Z');
    });

    // Still exactly 1 WS — no reconnect storm.
    expect(MockWebSocket.instances).toHaveLength(1);
    vi.useRealTimers();
  });

  // HIGH-4: AbortController — fetch is aborted on unmount.
  it('HIGH-4: aborts in-flight fetch on unmount', async () => {
    let capturedSignal: AbortSignal | undefined;
    vi.mocked(fetch).mockImplementation((...args) => {
      const init = args[1] as RequestInit | undefined;
      capturedSignal = init?.signal ?? undefined;
      // Return a promise that never resolves (simulates a slow fetch).
      return new Promise(() => undefined);
    });

    let capturedLoader: Parameters<typeof mockSetDataLoader>[0] | null = null;
    mockSetDataLoader.mockImplementation((loader: Parameters<typeof mockSetDataLoader>[0]) => {
      capturedLoader = loader;
    });

    const { unmount } = render(<TradeChart canonicalId="AAPL.US" />);

    // Trigger getBars so a fetch is in flight.
    const callback = vi.fn();
    await act(async () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (capturedLoader as any)?.getBars({ callback });
    });

    expect(capturedSignal).toBeDefined();
    expect(capturedSignal?.aborted).toBe(false);

    unmount();

    expect(capturedSignal?.aborted).toBe(true);
  });

  // HIGH-5: getJwt callback — WS uses cookie value at connect time (not a captured string).
  it('HIGH-5: opens WS using JWT read from cookie at connect time', async () => {
    await act(async () => {
      render(<TradeChart canonicalId="AAPL.US" />);
    });

    const ws = MockWebSocket.instances[0];
    // The WS should carry the JWT from the cookie as a bearer subprotocol.
    expect(ws?.protocols).toContain('bearer.test-jwt');
  });
});
