import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useBacktestStream } from './useBacktestStream';

describe('useBacktestStream', () => {
  let mockWs: {
    onmessage: ((e: MessageEvent) => void) | null;
    onclose: (() => void) | null;
    close: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    mockWs = { onmessage: null, onclose: null, close: vi.fn() };
    vi.stubGlobal(
      'WebSocket',
      vi.fn(function MockWS() {
        return mockWs;
      }),
    );
  });

  it('calls onDone with report when done frame received', () => {
    const onDone = vi.fn();
    renderHook(() =>
      useBacktestStream({
        botId: 'b1',
        jobId: 'j1',
        onDone,
        onFailed: vi.fn(),
        onProgress: vi.fn(),
      }),
    );

    act(() => {
      mockWs.onmessage?.({
        data: JSON.stringify({ type: 'done', report: { sharpe: 1.2 } }),
      } as MessageEvent);
    });
    expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ sharpe: 1.2 }));
  });

  it('calls onFailed when failed frame received', () => {
    const onFailed = vi.fn();
    renderHook(() =>
      useBacktestStream({
        botId: 'b1',
        jobId: 'j1',
        onDone: vi.fn(),
        onFailed,
        onProgress: vi.fn(),
      }),
    );

    act(() => {
      mockWs.onmessage?.({
        data: JSON.stringify({ type: 'failed', error_msg: 'oops' }),
      } as MessageEvent);
    });
    expect(onFailed).toHaveBeenCalledWith('oops');
  });

  it('reconnects on close with backoff', () => {
    vi.useFakeTimers();
    renderHook(() =>
      useBacktestStream({
        botId: 'b1',
        jobId: 'j1',
        onDone: vi.fn(),
        onFailed: vi.fn(),
        onProgress: vi.fn(),
      }),
    );
    act(() => {
      mockWs.onclose?.();
    });
    expect(WebSocket).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(600);
    });
    expect(WebSocket).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });
});
