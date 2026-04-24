import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MockQuotesService } from './quotes';

describe('MockQuotesService', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(()  => { vi.useRealTimers(); });

  it('getSnapshot returns seeded quote', () => {
    const svc = new MockQuotesService();
    expect(svc.getSnapshot('AAPL')).toBeDefined();
    expect(svc.getSnapshot('NONEXIST')).toBeUndefined();
  });

  it('timer does not start without subscribers', () => {
    const svc = new MockQuotesService();
    vi.advanceTimersByTime(2000);
    expect(svc.getSnapshot('AAPL')?.last).toBeGreaterThan(0);
  });

  it('subscribe starts timer and emits tick', () => {
    const svc = new MockQuotesService();
    const cb = vi.fn();
    svc.subscribe(['AAPL'], cb);
    vi.advanceTimersByTime(600);
    expect(cb).toHaveBeenCalled();
  });

  it('unsubscribe stops timer when refcount hits zero', () => {
    const svc = new MockQuotesService();
    const cb = vi.fn();
    const unsub = svc.subscribe(['AAPL'], cb);
    vi.advanceTimersByTime(600);
    unsub();
    cb.mockClear();
    vi.advanceTimersByTime(2000);
    expect(cb).not.toHaveBeenCalled();
  });

  it('setTickingEnabled(false) stops timer immediately', () => {
    const svc = new MockQuotesService();
    const cb = vi.fn();
    svc.subscribe(['AAPL'], cb);
    vi.advanceTimersByTime(600);
    cb.mockClear();
    svc.setTickingEnabled(false);
    vi.advanceTimersByTime(2000);
    expect(cb).not.toHaveBeenCalled();
  });
});
