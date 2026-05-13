import { act, renderHook, waitFor } from '@testing-library/react';
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';

import { useAlertsFeed } from '@/hooks/useAlertsFeed';
import { useAlertsStore } from '@/stores/global/alerts';

vi.mock('@/services/alerts/api', () => ({
  getRecentFires: vi.fn(),
}));

import { getRecentFires } from '@/services/alerts/api';

interface FakeWS {
  url: string;
  readyState: number;
  onopen: ((ev: Event) => void) | null;
  onmessage: ((ev: MessageEvent<string>) => void) | null;
  onclose: ((ev: CloseEvent) => void) | null;
  onerror: ((ev: Event) => void) | null;
  close: ReturnType<typeof vi.fn>;
}

function installWebSocketMock(): FakeWS[] {
  const sockets: FakeWS[] = [];
  class FakeWebSocket {
    static readonly OPEN = 1;
    url: string;
    readyState = FakeWebSocket.OPEN;
    onopen: FakeWS['onopen'] = null;
    onmessage: FakeWS['onmessage'] = null;
    onclose: FakeWS['onclose'] = null;
    onerror: FakeWS['onerror'] = null;
    close = vi.fn(() => {
      this.readyState = 3;
    });

    constructor(url: string) {
      this.url = url;
      sockets.push(this as unknown as FakeWS);
    }
  }
  vi.stubGlobal('WebSocket', FakeWebSocket);
  return sockets;
}

function sameOriginWs(path: string): string {
  return `ws://${window.location.host}${path}`;
}

describe('useAlertsFeed', () => {
  beforeEach(() => {
    useAlertsStore.setState({ recentFires: [], lastSeenAt: null });
    vi.mocked(getRecentFires).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('backfills via getRecentFires(lastSeenAt) before opening WS', async () => {
    vi.mocked(getRecentFires).mockResolvedValue({
      fires: [
        { id: 99, alert_id: 1, fired_at: '2026-05-13T13:00:00Z', verdict: 'true' },
      ],
    });
    useAlertsStore.setState({
      recentFires: [],
      lastSeenAt: '2026-05-13T12:00:00Z',
    });

    const sockets = installWebSocketMock();
    renderHook(() => useAlertsFeed({ wsUrl: sameOriginWs('/ws/alerts/feed') }));

    await waitFor(() => {
      expect(getRecentFires).toHaveBeenCalledWith('2026-05-13T12:00:00Z', 50);
    });
    await waitFor(() => {
      expect(useAlertsStore.getState().recentFires.map((f) => f.id)).toContain(99);
    });
    expect(sockets.length).toBeGreaterThan(0);
  });

  it('appends fire frame and updates lastSeenAt', async () => {
    vi.mocked(getRecentFires).mockResolvedValue({ fires: [] });
    const sockets = installWebSocketMock();
    renderHook(() => useAlertsFeed({ wsUrl: sameOriginWs('/ws/alerts/feed') }));

    await waitFor(() => {
      expect(sockets.length).toBeGreaterThan(0);
    });
    const ws = sockets[0];
    if (ws === undefined) throw new Error('ws not created');

    act(() => ws.onopen?.(new Event('open')));
    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({
          v: 1,
          type: 'fire',
          fire_id: 42,
          alert_id: 7,
          user_label: 'AAPL',
          verdict: 'true',
          evaluated_values: { close: 200 },
          fired_at: '2026-05-13T14:00:00Z',
        }),
      } as MessageEvent<string>);
    });

    await waitFor(() => {
      const fires = useAlertsStore.getState().recentFires;
      expect(fires.map((f) => f.id)).toContain(42);
      expect(useAlertsStore.getState().lastSeenAt).toBe('2026-05-13T14:00:00Z');
    });
  });

  it('drops malformed frames and closes socket', async () => {
    vi.mocked(getRecentFires).mockResolvedValue({ fires: [] });
    const sockets = installWebSocketMock();
    renderHook(() => useAlertsFeed({ wsUrl: sameOriginWs('/ws/alerts/feed') }));

    await waitFor(() => expect(sockets.length).toBeGreaterThan(0));
    const ws = sockets[0];
    if (ws === undefined) throw new Error('ws not created');

    act(() => ws.onopen?.(new Event('open')));
    act(() => {
      ws.onmessage?.({ data: 'not-json' } as MessageEvent<string>);
    });
    expect(ws.close).toHaveBeenCalled();
  });

  it('rejects non-same-origin wsUrl with invalid_ws_url error', async () => {
    vi.mocked(getRecentFires).mockResolvedValue({ fires: [] });
    installWebSocketMock();
    const { result } = renderHook(() =>
      useAlertsFeed({ wsUrl: 'ws://evil.example/ws/alerts/feed' }),
    );
    await waitFor(() => {
      expect(result.current.error).toBe('invalid_ws_url');
    });
  });
});
