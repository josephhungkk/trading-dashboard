import { render, screen } from '@testing-library/react';
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SizingCalculatorPage } from '@/features/sizing/SizingCalculatorPage';

function renderPage(initialUrl = '/trade/sizing'): void {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const sizingRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/trade/sizing',
    component: SizingCalculatorPage,
    validateSearch: (search: Record<string, unknown>) => ({
      account_id: typeof search.account_id === 'string' ? search.account_id : undefined,
      instrument_id:
        typeof search.instrument_id === 'string'
          ? Number.parseInt(search.instrument_id, 10) || undefined
          : typeof search.instrument_id === 'number'
            ? search.instrument_id
            : undefined,
      side: search.side === 'sell' ? ('sell' as const) : ('buy' as const),
      entry: typeof search.entry === 'string' ? search.entry : undefined,
      stop: typeof search.stop === 'string' ? search.stop : undefined,
    }),
  });
  const routeTree = rootRoute.addChildren([sizingRoute]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [initialUrl] }),
  });
  render(<RouterProvider router={router as never} />);
}

describe('SizingCalculatorPage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders the 3 method columns', async () => {
    renderPage();
    expect(await screen.findByTestId('column-fixed_fractional')).toBeInTheDocument();
    expect(screen.getByTestId('column-risk_per_trade')).toBeInTheDocument();
    expect(screen.getByTestId('column-vol_targeted')).toBeInTheDocument();
  });

  it('renders the shared inputs (account, instrument, side, entry)', async () => {
    renderPage();
    expect(await screen.findByTestId('page-account-id')).toBeInTheDocument();
    expect(screen.getByTestId('page-instrument-id')).toBeInTheDocument();
    expect(screen.getByTestId('page-side')).toBeInTheDocument();
    expect(screen.getByTestId('page-entry')).toBeInTheDocument();
  });
});
