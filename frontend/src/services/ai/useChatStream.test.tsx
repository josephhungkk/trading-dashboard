import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useChatStream } from '@/services/ai/useChatStream';

interface FakeWS {
  url: string;
  readyState: number;
  sent: string[];
  onopen: ((ev: Event) => void) | null;
  onmessage: ((ev: MessageEvent<string>) => void) | null;
  onclose: ((ev: CloseEvent) => void) | null;
  onerror: ((ev: Event) => void) | null;
  send: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
}

function installWebSocketMock(): FakeWS[] {
  const sockets: FakeWS[] = [];

  class FakeWebSocket {
    static readonly OPEN = 1;
    url: string;
    readyState = FakeWebSocket.OPEN;
    sent: string[] = [];
    onopen: FakeWS['onopen'] = null;
    onmessage: FakeWS['onmessage'] = null;
    onclose: FakeWS['onclose'] = null;
    onerror: FakeWS['onerror'] = null;
    send = vi.fn((data: string) => {
      this.sent.push(data);
    });
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

function sameOriginWsUrl(path: string): string {
  return `ws://${window.location.host}${path}`;
}

describe('useChatStream', () => {
  beforeEach(() => {
    installWebSocketMock();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('sends chat and accumulates chunks until done', async () => {
    const sockets = installWebSocketMock();
    const { result } = renderHook(() =>
      useChatStream({ wsUrl: sameOriginWsUrl('/chat') }),
    );
    const ws = sockets[0];
    if (ws === undefined) throw new Error('websocket was not created');

    act(() => ws.onopen?.(new Event('open')));
    act(() => result.current.send([{ role: 'user', content: 'hi' }], 'REASONING'));

    expect(JSON.parse(ws.sent[0] ?? '{}')).toEqual({
      messages: [{ role: 'user', content: 'hi' }],
      capability: 'REASONING',
    });

    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({
          version: 1,
          type: 'chunk',
          text: 'hel',
          request_id: 'req-1',
        }),
      } as MessageEvent<string>);
      ws.onmessage?.({
        data: JSON.stringify({
          version: 1,
          type: 'chunk',
          text: 'lo',
          request_id: 'req-1',
        }),
      } as MessageEvent<string>);
      ws.onmessage?.({
        data: JSON.stringify({ version: 1, type: 'done', request_id: 'req-1' }),
      } as MessageEvent<string>);
    });

    await waitFor(() => expect(result.current.partial).toBe('hello'));
    expect(result.current.done).toBe(true);
  });

  it('briefly marks TurnRateExceeded as rate limited', async () => {
    vi.useFakeTimers();
    const sockets = installWebSocketMock();
    const { result } = renderHook(() =>
      useChatStream({ wsUrl: sameOriginWsUrl('/chat') }),
    );
    const ws = sockets[0];
    if (ws === undefined) throw new Error('websocket was not created');

    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({
          version: 1,
          type: 'error',
          error_class: 'TurnRateExceeded',
          message: 'wait a moment',
        }),
      } as MessageEvent<string>);
    });

    expect(result.current.rateLimited).toBe(true);
    act(() => vi.advanceTimersByTime(3_000));
    expect(result.current.rateLimited).toBe(false);
  });

  it('reconnects after a 500ms backoff when the websocket drops', () => {
    vi.useFakeTimers();
    const sockets = installWebSocketMock();
    renderHook(() => useChatStream({ wsUrl: sameOriginWsUrl('/chat') }));
    const ws = sockets[0];
    if (ws === undefined) throw new Error('websocket was not created');

    act(() => ws.onclose?.({} as CloseEvent));
    expect(sockets).toHaveLength(1);

    act(() => vi.advanceTimersByTime(500));
    expect(sockets).toHaveLength(2);
  });

  it('does not set state after unmount during a stream', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const sockets = installWebSocketMock();
    const { unmount } = renderHook(() =>
      useChatStream({ wsUrl: sameOriginWsUrl('/chat') }),
    );
    const ws = sockets[0];
    if (ws === undefined) throw new Error('websocket was not created');

    unmount();

    expect(() => {
      ws.onmessage?.({
        data: JSON.stringify({
          version: 1,
          type: 'chunk',
          text: 'late',
          request_id: 'req-1',
        }),
      } as MessageEvent<string>);
    }).not.toThrow();
    expect(consoleError).not.toHaveBeenCalled();
  });

  it('rejects cross-origin websocket urls', () => {
    const sockets = installWebSocketMock();
    const consoleWarn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);

    const { result } = renderHook(() =>
      useChatStream({ wsUrl: 'ws://example.test/chat' }),
    );

    expect(sockets).toHaveLength(0);
    expect(result.current.error).toBe('invalid_ws_url');
    expect(consoleWarn).toHaveBeenCalledWith(
      '[useChatStream] rejecting non-same-origin wsUrl',
      'ws://example.test/chat',
    );
  });

  it('sets protocol_version_mismatch and does not reconnect on version mismatch', () => {
    vi.useFakeTimers();
    const sockets = installWebSocketMock();
    const consoleWarn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const { result } = renderHook(() =>
      useChatStream({ wsUrl: sameOriginWsUrl('/chat') }),
    );
    const ws = sockets[0];
    if (ws === undefined) throw new Error('websocket was not created');

    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({ version: 2, type: 'done', request_id: 'req-1' }),
      } as MessageEvent<string>);
      ws.onclose?.({} as CloseEvent);
      vi.advanceTimersByTime(15_000);
    });

    expect(result.current.error).toBe('protocol_version_mismatch');
    expect(sockets).toHaveLength(1);
    expect(consoleWarn).toHaveBeenCalledWith(
      '[useChatStream] protocol version mismatch — closing',
      2,
    );
  });
});
