import type { QueryClient } from '@tanstack/react-query';
import { QueryClientProvider, QueryClient as TanStackQueryClient } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import { createElement } from 'react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AdvisorWsFrame } from '../../../services/advisor/types';
import { useAdvisorStream } from './useAdvisorStream';

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  onclose: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  close = vi.fn();

  constructor(public readonly url: string) {
    MockWebSocket.instances.push(this);
  }
}

function makeFrame(overrides: Partial<AdvisorWsFrame> = {}): AdvisorWsFrame {
  return {
    v: 1,
    bot_id: 'bot-1',
    account_id: 'acct-1',
    canonical_id: 'intent-1',
    verdict: 'approve',
    reasoning: 'looks fine',
    confidence: 0.91,
    advice_tags: [],
    latency_ms: 42,
    mode: 'OBSERVE',
    decision_id: 7,
    ...overrides,
  };
}

describe('useAdvisorStream', () => {
  const originalWebSocket = globalThis.WebSocket;
  let queryClient: QueryClient;
  let invalidateQueries: ReturnType<typeof vi.fn>;

  function wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  }

  beforeEach(() => {
    vi.clearAllMocks();
    MockWebSocket.instances = [];
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    queryClient = new TanStackQueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    invalidateQueries = vi.fn();
    queryClient.invalidateQueries = invalidateQueries as QueryClient['invalidateQueries'];
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
  });

  it('does not connect when botId is undefined', () => {
    renderHook(() => useAdvisorStream(undefined), { wrapper });

    expect(MockWebSocket.instances).toHaveLength(0);
  });

  it('connects to correct WS URL when botId provided', () => {
    renderHook(() => useAdvisorStream('bot-1'), { wrapper });

    expect(MockWebSocket.instances[0]?.url).toBe(
      `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/bots/bot-1/advisor`,
    );
  });

  it('drops frames with v !== 1', () => {
    renderHook(() => useAdvisorStream('bot-1'), { wrapper });

    act(() => {
      MockWebSocket.instances[0]?.onmessage?.(
        new MessageEvent('message', { data: JSON.stringify({ ...makeFrame(), v: 2 }) }),
      );
    });

    expect(invalidateQueries).not.toHaveBeenCalled();
  });

  it('calls invalidateQueries on valid frame', () => {
    renderHook(() => useAdvisorStream('bot-1'), { wrapper });

    act(() => {
      MockWebSocket.instances[0]?.onmessage?.(
        new MessageEvent('message', { data: JSON.stringify(makeFrame()) }),
      );
    });

    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['bot', 'bot-1', 'advisor-decisions'],
    });
  });

  it('closes WS on unmount', () => {
    const { unmount } = renderHook(() => useAdvisorStream('bot-1'), { wrapper });
    const ws = MockWebSocket.instances[0];

    unmount();

    expect(ws?.close).toHaveBeenCalledTimes(1);
  });
});
