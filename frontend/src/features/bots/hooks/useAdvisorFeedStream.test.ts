import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AdvisorWsFrame } from '../../../services/advisor/types';
import { useAdvisorFeedStream } from './useAdvisorFeedStream';

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  onclose: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onopen: (() => void) | null = null;
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

describe('useAdvisorFeedStream', () => {
  const originalWebSocket = globalThis.WebSocket;

  beforeEach(() => {
    vi.clearAllMocks();
    MockWebSocket.instances = [];
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
  });

  it('connects to /ws/bots/advisor', () => {
    renderHook(() => useAdvisorFeedStream());

    expect(MockWebSocket.instances[0]?.url).toBe(
      `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/bots/advisor`,
    );
  });

  it('drops frames with v !== 1', () => {
    const { result } = renderHook(() => useAdvisorFeedStream());

    act(() => {
      MockWebSocket.instances[0]?.onmessage?.(
        new MessageEvent('message', { data: JSON.stringify({ ...makeFrame(), v: 2 }) }),
      );
    });

    expect(result.current.frames).toEqual([]);
  });

  it('accumulates frames max 200', () => {
    const { result } = renderHook(() => useAdvisorFeedStream());

    act(() => {
      for (let decisionId = 0; decisionId < 205; decisionId++) {
        MockWebSocket.instances[0]?.onmessage?.(
          new MessageEvent('message', {
            data: JSON.stringify(makeFrame({ decision_id: decisionId })),
          }),
        );
      }
    });

    expect(result.current.frames).toHaveLength(200);
    expect(result.current.frames[0]?.decision_id).toBe(204);
    expect(result.current.frames[199]?.decision_id).toBe(5);
  });

  it('closes WS on unmount', () => {
    const { unmount } = renderHook(() => useAdvisorFeedStream());
    const ws = MockWebSocket.instances[0];

    unmount();

    expect(ws?.close).toHaveBeenCalledTimes(1);
  });
});
