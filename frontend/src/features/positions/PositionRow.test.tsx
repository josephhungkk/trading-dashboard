import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
  Outlet,
} from '@tanstack/react-router';
import { PositionRow } from './PositionRow';
import type { PositionRowData } from './PositionRow';
// ViewChartLink is now used inside PositionRow (MED-F); row tests exercise it via integration.

function makePosition(overrides: Partial<PositionRowData> = {}): PositionRowData {
  return {
    accountId: 'acct-1',
    symbol: 'AAPL',
    qty: 10,
    avgCost: 180.5,
    marketValue: 1900,
    pnlUnrealized: 95,
    pnlRealized: 0,
    currency: 'USD',
    asOf: '2026-05-07T00:00:00Z',
    ...overrides,
  };
}

function renderWithRouter(ui: React.ReactNode): void {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/',
    component: () => <>{ui}</>,
  });
  const chartRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/chart/$canonicalId',
    component: () => <div data-testid="chart-page" />,
  });
  const routeTree = rootRoute.addChildren([indexRoute, chartRoute]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ['/'] }),
  });
  render(<RouterProvider router={router as never} />);
}

describe('PositionRow', () => {
  it('renders View Chart link with correct canonical_id', async () => {
    renderWithRouter(
      <PositionRow position={makePosition({ symbol: 'AAPL', canonical_id: 'AAPL.US' })} />,
    );
    const link = await screen.findByRole('link', { name: /view chart/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', expect.stringContaining('/chart/AAPL.US'));
  });

  it('omits link when canonical_id is null', async () => {
    renderWithRouter(
      <PositionRow position={makePosition({ symbol: 'AAPL', canonical_id: null })} />,
    );
    await screen.findByText('AAPL');
    expect(screen.queryByRole('link', { name: /view chart/i })).not.toBeInTheDocument();
  });

  it('omits link when canonical_id is undefined', async () => {
    renderWithRouter(
      <PositionRow position={makePosition({ symbol: 'MSFT' })} />,
    );
    await screen.findByText('MSFT');
    expect(screen.queryByRole('link', { name: /view chart/i })).not.toBeInTheDocument();
  });

  it('link has accessible name "View Chart"', async () => {
    renderWithRouter(
      <PositionRow position={makePosition({ symbol: 'AAPL', canonical_id: 'AAPL.US' })} />,
    );
    expect(await screen.findByRole('link', { name: /view chart/i })).toBeInTheDocument();
  });
});
