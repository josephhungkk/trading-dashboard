import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTickingQuotes } from './use-ticking-quotes';
import { getServices, resetServices } from '@/services/registry';
import type { Quote } from '@/services/types';

describe('useTickingQuotes', () => {
  let rafCallbacks: FrameRequestCallback[] = [];
  let origRaf: typeof globalThis.requestAnimationFrame;
  let origCancel: typeof globalThis.cancelAnimationFrame;

  beforeEach(() => {
    resetServices();
    rafCallbacks = [];
    origRaf = globalThis.requestAnimationFrame;
    origCancel = globalThis.cancelAnimationFrame;
    globalThis.requestAnimationFrame = ((cb: FrameRequestCallback): number => {
      rafCallbacks.push(cb);
      return rafCallbacks.length;
    }) as typeof globalThis.requestAnimationFrame;
    globalThis.cancelAnimationFrame = vi.fn() as typeof globalThis.cancelAnimationFrame;
  });

  afterEach(() => {
    globalThis.requestAnimationFrame = origRaf;
    globalThis.cancelAnimationFrame = origCancel;
  });

  function flushRaf(): void {
    const pending = rafCallbacks;
    rafCallbacks = [];
    for (const cb of pending) cb(0);
  }

  it('seeds snapshot from svc.getSnapshot on mount', () => {
    const svc = getServices().quotes;
    const aapl = svc.getSnapshot('AAPL');
    const { result } = renderHook(() => useTickingQuotes(['AAPL', 'XXNO_SUCH']));
    expect(result.current['AAPL']).toBeDefined();
    expect(result.current['AAPL']?.symbol).toBe(aapl?.symbol ?? 'AAPL');
    expect(result.current['XXNO_SUCH']).toBeUndefined();
  });

  it('updates snapshot when the service emits a tick (rAF-flushed)', () => {
    const svc = getServices().quotes;
    let lastCallback: ((q: Quote) => void) | null = null;
    const origSubscribe = svc.subscribe.bind(svc);
    const spy = vi
      .spyOn(svc, 'subscribe')
      .mockImplementation((symbols: string[], cb: (q: Quote) => void) => {
        lastCallback = cb;
        return origSubscribe(symbols, () => { /* real impl ignored for this test */ });
      });

    const { result } = renderHook(() => useTickingQuotes(['AAPL']));
    const original = result.current['AAPL'];
    const nextLast = (original?.last ?? 100) + 5;
    expect(lastCallback).not.toBeNull();
    if (!lastCallback) return;
    const emit = lastCallback;

    act(() => {
      emit({ ...(original as Quote), symbol: 'AAPL', last: nextLast });
      flushRaf();
    });

    expect(result.current['AAPL']?.last).toBe(nextLast);
    spy.mockRestore();
  });

  it('unsubscribes on unmount', () => {
    const svc = getServices().quotes;
    const unsub = vi.fn();
    vi.spyOn(svc, 'subscribe').mockReturnValue(unsub);
    const { unmount } = renderHook(() => useTickingQuotes(['AAPL']));
    unmount();
    expect(unsub).toHaveBeenCalledTimes(1);
  });
});
