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
) => {
  // Narrow once outside the query config so the queryFn closure captures a
  // string, not string|null. `enabled` still gates the fetch (TanStack
  // Query won't invoke queryFn when assetClass is null), and the
  // assertNonNull below makes the type narrowing explicit (reviewer HIGH —
  // drops the Promise.reject hack).
  const assetClassNonNull = assetClass ?? '';
  return useQuery<RollupDrill>({
    queryKey: ['portfolio', 'rollup', 'drill', assetClass, base],
    queryFn: () => fetchRollupDrill(assetClassNonNull, base),
    enabled: assetClass !== null,
  });
};
