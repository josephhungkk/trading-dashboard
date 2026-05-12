/**
 * Phase 10b.2 — TanStack-Query wrapper for /api/portfolio/rollup/drill.
 * Lazy: `enabled` gates the request on a selected asset class so the
 * drawer only fires when a user clicks an asset-class row.
 */

import { useQuery } from '@tanstack/react-query';

import { fetchRollupDrill } from '@/services/portfolio/api';
import type { BaseCurrency, RollupDrill } from '@/services/portfolio/types';

export const useRollupDrill = (
  assetClass: string | null,
  base: BaseCurrency,
) =>
  useQuery<RollupDrill>({
    queryKey: ['portfolio', 'rollup', 'drill', assetClass, base],
    queryFn: () => {
      if (assetClass === null) {
        // Cannot fire because `enabled` is false; this is a type narrow only.
        return Promise.reject(new Error('asset_class is null'));
      }
      return fetchRollupDrill(assetClass, base);
    },
    enabled: assetClass !== null,
  });
