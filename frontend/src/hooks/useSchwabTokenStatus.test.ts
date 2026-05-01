import { act, renderHook } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('@/services/schwab', () => ({
  getTokenStatus: vi.fn(async () => ({
    accessTokenIssuedAt: new Date('2026-04-30T12:00:00Z'),
    refreshTokenIssuedAt: new Date('2026-04-30T12:00:00Z'),
    tier2RefreshEnabled: false,
    tier2ConsecutiveFailures: 0,
  })),
  subscribeConfigStream: vi.fn(() => () => undefined),
}));

import { useSchwabTokenStatus } from './useSchwabTokenStatus';
import * as schwab from '@/services/schwab';

describe('useSchwabTokenStatus', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it('calls getTokenStatus on mount', async () => {
    renderHook(() => useSchwabTokenStatus());
    await vi.runOnlyPendingTimersAsync();
    expect(schwab.getTokenStatus).toHaveBeenCalled();
  });

  it('returns the parsed status after initial fetch', async () => {
    const { result } = renderHook(() => useSchwabTokenStatus());
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.loading).toBe(false);
    expect(result.current.status?.tier2RefreshEnabled).toBe(false);
  });

  it('subscribes to the config stream for the schwab namespace', async () => {
    renderHook(() => useSchwabTokenStatus());
    expect(schwab.subscribeConfigStream).toHaveBeenCalledWith(
      'schwab',
      expect.any(Function),
    );
  });
});
