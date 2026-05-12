/**
 * Phase 10b.2 — AssetClassDrillDrawer component tests (3).
 *   1. opens when assetClass is non-null (renders title)
 *   2. Escape key fires onClose
 *   3. block-verdict rows get the red tint
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AssetClassDrillDrawer } from '@/features/portfolio/AssetClassDrillDrawer';
import * as api from '@/services/portfolio/api';
import type { RollupDrill } from '@/services/portfolio/types';

function makeDrill(
  overrides?: Partial<RollupDrill['instruments'][number]>,
): RollupDrill {
  return {
    asset_class: 'STOCK',
    base_currency: 'GBP',
    instruments: [
      {
        instrument_id: 1,
        display_name: 'AAPL',
        exchange: 'NASDAQ',
        total_qty: '100',
        notional_base: '12345.00',
        pct_of_nlv: '12.34',
        cap_pct: null,
        utilisation_pct: null,
        verdict: 'ok',
        ...overrides,
      },
    ],
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

describe('AssetClassDrillDrawer', () => {
  afterEach(() => vi.restoreAllMocks());

  it('opens when assetClass is non-null', async () => {
    vi.spyOn(api, 'fetchRollupDrill').mockResolvedValue(makeDrill());

    render(
      <AssetClassDrillDrawer
        assetClass="STOCK"
        base="GBP"
        onClose={vi.fn()}
      />,
      { wrapper: makeWrapper() },
    );

    expect(screen.getByTestId('rollup-drill-title')).toHaveTextContent('STOCK');
    await waitFor(() => screen.getByTestId('rollup-drill-row-1'));
  });

  it('Escape key fires onClose', () => {
    vi.spyOn(api, 'fetchRollupDrill').mockResolvedValue(makeDrill());
    const onClose = vi.fn();

    render(
      <AssetClassDrillDrawer
        assetClass="STOCK"
        base="GBP"
        onClose={onClose}
      />,
      { wrapper: makeWrapper() },
    );

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('block-verdict rows get the red tint', async () => {
    vi.spyOn(api, 'fetchRollupDrill').mockResolvedValue(
      makeDrill({ verdict: 'block' }),
    );

    render(
      <AssetClassDrillDrawer
        assetClass="STOCK"
        base="GBP"
        onClose={vi.fn()}
      />,
      { wrapper: makeWrapper() },
    );

    const row = await waitFor(() => screen.getByTestId('rollup-drill-row-1'));
    expect(row.className).toMatch(/bg-red-50/);
  });
});
