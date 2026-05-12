/**
 * Phase 10b.2 — portfolio rollup UI state (persisted base currency).
 * MED-7 (architect): migrate callback validates persisted base against the
 * supported set so a stale localStorage value can't poison the API call.
 */

import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

import type { BaseCurrency } from '@/services/portfolio/types';
import { SUPPORTED_BASES } from '@/services/portfolio/types';

const SUPPORTED: ReadonlySet<BaseCurrency> = new Set(SUPPORTED_BASES);

interface PortfolioStore {
  portfolioRollupBase: BaseCurrency;
  setBase: (b: BaseCurrency) => void;
}

export const usePortfolioStore = create<PortfolioStore>()(
  persist(
    (set) => ({
      portfolioRollupBase: 'GBP',
      setBase: (b: BaseCurrency) => set({ portfolioRollupBase: b }),
    }),
    {
      name: 'portfolio-rollup',
      storage: createJSONStorage(() => localStorage),
      version: 1,
      migrate: (state: unknown) => {
        const s = state as { portfolioRollupBase?: BaseCurrency } | null;
        if (!s?.portfolioRollupBase || !SUPPORTED.has(s.portfolioRollupBase)) {
          return { portfolioRollupBase: 'GBP' as BaseCurrency };
        }
        return s;
      },
    },
  ),
);
