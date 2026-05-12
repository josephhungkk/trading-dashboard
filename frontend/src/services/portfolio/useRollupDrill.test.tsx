/**
 * Phase 10b.2 — useRollupDrill hook tests.
 * Lazy hook — verify `enabled` gates the fetch and the data flows on
 * non-null assetClass.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/services/portfolio/api';
import type { RollupDrill } from '@/services/portfolio/types';
import { useRollupDrill } from '@/services/portfolio/useRollupDrill';

function makeDrill(): RollupDrill {
  return {
    asset_class: 'STOCK',
    base_currency: 'GBP',
    instruments: [],
  } as RollupDrill;
}

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  }
  return Wrapper;
}

describe('useRollupDrill', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('does not fetch when assetClass is null', () => {
    const spy = vi.spyOn(api, 'fetchRollupDrill').mockResolvedValue(makeDrill());

    const { result } = renderHook(() => useRollupDrill(null, 'GBP'), {
      wrapper: makeWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
    expect(spy).not.toHaveBeenCalled();
  });

  it('fetches when assetClass is provided', async () => {
    vi.spyOn(api, 'fetchRollupDrill').mockResolvedValue(makeDrill());

    const { result } = renderHook(() => useRollupDrill('STOCK', 'GBP'), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.asset_class).toBe('STOCK');
    expect(result.current.data?.base_currency).toBe('GBP');
  });
});
