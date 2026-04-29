import { useCallback, useState } from 'react';
import type { components } from '@/services/api-generated';
import { fetchFills } from '@/services/api';

type FillResponse = components['schemas']['FillResponse'];
type FillListResponse = components['schemas']['FillListResponse'];

export interface UseFillsHistoryParams {
  accountId: string;
  from: string;
  to: string;
  pageSize?: number;
}

export interface UseFillsHistoryResult {
  fills: FillResponse[];
  isLoading: boolean;
  error: Error | null;
  hasMore: boolean;
  loadMore: () => Promise<void>;
}

export function useFillsHistory(params: UseFillsHistoryParams): UseFillsHistoryResult {
  const [fills, setFills] = useState<FillResponse[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [hasMore, setHasMore] = useState(true);

  const loadMore = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response: FillListResponse = await fetchFills({
        account_id: params.accountId,
        from: params.from,
        to: params.to,
        limit: params.pageSize ?? 100,
        ...(cursor !== null ? { cursor } : {}),
      });
      setFills(prev => [...prev, ...response.fills]);
      const next = response.next_cursor ?? null;
      setCursor(next);
      setHasMore(next !== null);
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setIsLoading(false);
    }
  }, [params.accountId, params.from, params.to, params.pageSize, cursor]);

  return { fills, isLoading, error, hasMore, loadMore };
}
