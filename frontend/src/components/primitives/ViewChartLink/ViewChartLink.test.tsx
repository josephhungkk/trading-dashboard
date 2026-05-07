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
import { ViewChartLink } from './ViewChartLink';

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

describe('ViewChartLink', () => {
  it('renders link with correct href when canonicalId is provided', async () => {
    renderWithRouter(<ViewChartLink canonicalId="AAPL.US" />);
    const link = await screen.findByRole('link', { name: /view chart/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', expect.stringContaining('/chart/AAPL.US'));
  });

  it('renders nothing when canonicalId is null', async () => {
    renderWithRouter(<ViewChartLink canonicalId={null} />);
    await screen.findByRole('main').catch(() => null);
    expect(screen.queryByRole('link', { name: /view chart/i })).not.toBeInTheDocument();
  });

  it('renders nothing when canonicalId is undefined', async () => {
    renderWithRouter(<ViewChartLink canonicalId={undefined} />);
    await screen.findByRole('main').catch(() => null);
    expect(screen.queryByRole('link', { name: /view chart/i })).not.toBeInTheDocument();
  });

  it('link has accessible aria-label "View Chart"', async () => {
    renderWithRouter(<ViewChartLink canonicalId="TSLA.US" />);
    const link = await screen.findByRole('link', { name: 'View Chart' });
    expect(link).toBeInTheDocument();
  });

  it('renders LineChart icon with aria-hidden', async () => {
    renderWithRouter(<ViewChartLink canonicalId="MSFT.US" />);
    await screen.findByRole('link', { name: /view chart/i });
    const svg = document.querySelector('svg[aria-hidden="true"]');
    expect(svg).toBeInTheDocument();
  });
});
