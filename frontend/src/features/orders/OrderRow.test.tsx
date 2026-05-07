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
import { OrderRow } from './OrderRow';
import type { OrderRowData } from './OrderRow';
// ViewChartLink is now used inside OrderRow (MED-F); row tests exercise it via integration.

function makeOrder(overrides: Partial<OrderRowData> = {}): OrderRowData {
  return {
    id: 'ord-1',
    symbol: 'AAPL',
    side: 'BUY',
    qty: '10',
    status: 'submitted',
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

describe('OrderRow', () => {
  it('renders View Chart link with correct canonical_id', async () => {
    renderWithRouter(
      <OrderRow order={makeOrder({ symbol: 'AAPL', canonical_id: 'AAPL.US' })} />,
    );
    const link = await screen.findByRole('link', { name: /view chart/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', expect.stringContaining('/chart/AAPL.US'));
  });

  it('omits link when canonical_id is null', async () => {
    renderWithRouter(
      <OrderRow order={makeOrder({ symbol: 'AAPL', canonical_id: null })} />,
    );
    await screen.findByText('AAPL');
    expect(screen.queryByRole('link', { name: /view chart/i })).not.toBeInTheDocument();
  });

  it('omits link when canonical_id is undefined', async () => {
    renderWithRouter(
      <OrderRow order={makeOrder({ symbol: 'MSFT' })} />,
    );
    await screen.findByText('MSFT');
    expect(screen.queryByRole('link', { name: /view chart/i })).not.toBeInTheDocument();
  });

  it('link has accessible name "View Chart"', async () => {
    renderWithRouter(
      <OrderRow order={makeOrder({ symbol: 'AAPL', canonical_id: 'AAPL.US' })} />,
    );
    expect(await screen.findByRole('link', { name: /view chart/i })).toBeInTheDocument();
  });
});
