import { useQuery } from '@tanstack/react-query';

import { getSizingDefaults } from '@/services/sizing/api';
import type { SizingDefaults } from '@/services/sizing/types';

export function useSizingDefaults(accountId: string | undefined) {
  return useQuery<SizingDefaults>({
    queryKey: ['sizing-defaults', accountId],
    queryFn: () => {
      if (!accountId) {
        return Promise.reject(new Error('accountId is required'));
      }
      return getSizingDefaults(accountId);
    },
    enabled: Boolean(accountId),
    staleTime: 60_000,
  });
}
