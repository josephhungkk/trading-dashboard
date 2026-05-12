import { createFileRoute } from '@tanstack/react-router';

import { RollupPage } from '@/features/portfolio/RollupPage';
import type { CurveWindow } from '@/services/portfolio/types';

export interface PortfolioRollupSearch {
  window: CurveWindow;
}

function asWindow(value: unknown): CurveWindow {
  if (value === '30d' || value === '1y') return value;
  return 'intraday';
}

function validateSearch(search: Record<string, unknown>): PortfolioRollupSearch {
  return {
    window: asWindow(search.window),
  };
}

export const Route = createFileRoute('/portfolio/rollup')({
  component: RollupPage,
  validateSearch,
});
