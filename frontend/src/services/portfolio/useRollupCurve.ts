/**
 * Phase 10b.2 — TanStack-Query wrapper for /api/portfolio/rollup/curve.
 * Pure REST (no WS push) — curve windows are slow-changing and cheap to
 * refetch on window toggle.
 */

import { useQuery } from '@tanstack/react-query';

import { fetchRollupCurve } from '@/services/portfolio/api';
import type {
  BaseCurrency,
  CurveWindow,
  RollupCurve,
} from '@/services/portfolio/types';

export const useRollupCurve = (base: BaseCurrency, window: CurveWindow) =>
  useQuery<RollupCurve>({
    queryKey: ['portfolio', 'rollup', 'curve', base, window],
    queryFn: () => fetchRollupCurve(base, window),
  });
