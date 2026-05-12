/**
 * Phase 10b.2 — RollupPage component tests (2).
 *   1. renders KPI bar + curve + per-account + exposure list
 *   2. base-currency select updates the Zustand-persist store
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RollupPage } from '@/features/portfolio/RollupPage';
import * as api from '@/services/portfolio/api';
import type { RollupCurve, RollupLive } from '@/services/portfolio/types';

function makeRollup(): RollupLive {
  return {
    base_currency: 'GBP',
    total_nlv_base: '50000.00',
    total_realized_today_base: '100',
    total_unrealized_base: '250',
    history_since: '2026-05-12T00:00:00+00:00',
    accounts: [
      {
        account_id: 'a1',
        broker_id: 'ibkr',
        alias: 'IB Main',
        currency_native: 'USD',
        nlv_native: '60000',
        nlv_base: '47500.00',
        realized_today_base: '100',
        unrealized_base: '250',
        fx_rate: '0.79',
        fx_stale: false,
        nlv_age_s: 5,
        status: 'live',
      },
    ],
    exposure_by_asset_class: [
      {
        asset_class: 'STOCK',
        long_notional_base: '30000.00',
        short_notional_base: '0.00',
        pct_of_nlv: '60.00',
      },
    ],
    fx_rates: { 'USD/GBP': '0.79' },
    stale_accounts: [],
    fx_stale_accounts: [],
    partial: false,
  } as RollupLive;
}

function makeCurve(): RollupCurve {
  return {
    base_currency: 'GBP',
    window: 'intraday',
    per_account: [],
    totals: [
      { bucket: '2026-05-12T09:00:00+00:00', total_nlv_base: '49000' },
      { bucket: '2026-05-12T10:00:00+00:00', total_nlv_base: '50000' },
    ],
  } as RollupCurve;
}

function renderPage(): void {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const portfolioRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/portfolio/rollup',
    component: RollupPage,
    validateSearch: (search: Record<string, unknown>) => ({
      window:
        search.window === '30d' || search.window === '1y'
          ? (search.window as '30d' | '1y')
          : ('intraday' as const),
    }),
  });
  const routeTree = rootRoute.addChildren([portfolioRoute]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ['/portfolio/rollup'] }),
  });
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
}

describe('RollupPage', () => {
  beforeEach(() => {
    // jsdom has no WebSocket; provide a noop constructor (no body —
    // the field initializers are the only work needed).
    class FakeWebSocket {
      onopen: ((ev: Event) => void) | null = null;
      onmessage: ((ev: MessageEvent<string>) => void) | null = null;
      onclose: ((ev: CloseEvent) => void) | null = null;
      onerror: ((ev: Event) => void) | null = null;
      close = vi.fn();
    }
    vi.stubGlobal('WebSocket', FakeWebSocket);

    localStorage.clear();
    vi.spyOn(api, 'fetchRollupLive').mockResolvedValue(makeRollup());
    vi.spyOn(api, 'fetchRollupCurve').mockResolvedValue(makeCurve());
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('renders KPI bar + curve + per-account + exposure list', async () => {
    renderPage();

    await waitFor(() => screen.getByTestId('rollup-kpi-bar'));
    expect(screen.getByTestId('rollup-total-nlv')).toHaveTextContent(
      '50000.00',
    );
    expect(screen.getByTestId('rollup-curve-chart')).toBeInTheDocument();
    expect(screen.getByTestId('rollup-per-account-table')).toBeInTheDocument();
    expect(screen.getByTestId('rollup-exposure-list')).toBeInTheDocument();
    expect(screen.getByTestId('rollup-account-row-a1')).toHaveTextContent(
      'IB Main',
    );
    expect(
      screen.getByTestId('rollup-exposure-row-STOCK'),
    ).toBeInTheDocument();
  });

  it('base-currency select persists to localStorage', async () => {
    renderPage();

    const select = await waitFor(() => screen.getByTestId('rollup-base-select'));
    fireEvent.change(select, { target: { value: 'USD' } });

    expect(localStorage.getItem('portfolio-rollup')).toContain('USD');
  });
});
