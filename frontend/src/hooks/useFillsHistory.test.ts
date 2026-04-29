import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useFillsHistory } from './useFillsHistory';

vi.mock('@/services/api', () => ({
  fetchFills: vi.fn(),
}));

import { fetchFills } from '@/services/api';

const mockFetchFills = fetchFills as ReturnType<typeof vi.fn>;

const makeFill = (id: string) => ({
  id,
  exec_id: `exec-${id}`,
  currency: 'USD',
  executed_at: '2026-01-01T00:00:00Z',
  order_id: 'order-1',
  price: '100.00',
  qty: '10',
  side: 'BUY',
  symbol: 'AAPL',
});

describe('useFillsHistory', () => {
  const defaultParams = {
    accountId: 'acct-1',
    from: '2026-01-01T00:00:00Z',
    to: '2026-01-31T23:59:59Z',
    pageSize: 10,
  };

  beforeEach(() => {
    mockFetchFills.mockReset();
  });

  it('fetches first page and populates fills', async () => {
    mockFetchFills.mockResolvedValueOnce({
      fills: [makeFill('fill-1'), makeFill('fill-2')],
      next_cursor: null,
    });

    const { result } = renderHook(() => useFillsHistory(defaultParams));

    expect(result.current.fills).toHaveLength(0);
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.hasMore).toBe(true);

    await act(async () => {
      await result.current.loadMore();
    });

    expect(result.current.fills).toHaveLength(2);
    expect(result.current.fills[0]?.id).toBe('fill-1');
    expect(result.current.fills[1]?.id).toBe('fill-2');
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.hasMore).toBe(false);
  });

  it('appends on loadMore and sets hasMore false when no next cursor', async () => {
    mockFetchFills
      .mockResolvedValueOnce({
        fills: [makeFill('fill-1')],
        next_cursor: 'cursor-A',
      })
      .mockResolvedValueOnce({
        fills: [makeFill('fill-2')],
        next_cursor: null,
      });

    const { result } = renderHook(() => useFillsHistory(defaultParams));

    await act(async () => {
      await result.current.loadMore();
    });

    expect(result.current.fills).toHaveLength(1);
    expect(result.current.hasMore).toBe(true);

    // Verify cursor was passed on the second call
    await act(async () => {
      await result.current.loadMore();
    });

    expect(result.current.fills).toHaveLength(2);
    expect(result.current.fills[0]?.id).toBe('fill-1');
    expect(result.current.fills[1]?.id).toBe('fill-2');
    expect(result.current.hasMore).toBe(false);
    expect(mockFetchFills).toHaveBeenCalledTimes(2);
    expect(mockFetchFills).toHaveBeenNthCalledWith(2, expect.objectContaining({ cursor: 'cursor-A' }));
  });

  it('populates error state on fetch failure', async () => {
    const fetchError = new Error('network failure');
    mockFetchFills.mockRejectedValueOnce(fetchError);

    const { result } = renderHook(() => useFillsHistory(defaultParams));

    await act(async () => {
      await result.current.loadMore();
    });

    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.message).toBe('network failure');
    expect(result.current.fills).toHaveLength(0);
    expect(result.current.isLoading).toBe(false);
  });
});
