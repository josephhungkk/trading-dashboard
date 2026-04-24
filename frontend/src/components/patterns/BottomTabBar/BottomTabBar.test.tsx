import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
  Outlet,
} from '@tanstack/react-router';
import { BottomTabBar } from './BottomTabBar';

const TAB_PATHS = ['/overview', '/orders', '/positions', '/watchlist', '/more'] as const;

function renderTabBar(initialPath: string): void {
  const rootRoute = createRootRoute({
    component: () => (
      <div>
        <Outlet />
        <BottomTabBar />
      </div>
    ),
  });
  const childRoutes = TAB_PATHS.map((path) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path,
      component: () => <div data-testid={`page-${path}`}>{path}</div>,
    }),
  );
  const routeTree = rootRoute.addChildren(childRoutes);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [initialPath] }),
  });
  render(<RouterProvider router={router as never} />);
}

describe('BottomTabBar', () => {
  it('renders 5 tabs', async () => {
    renderTabBar('/overview');
    await waitFor(() => {
      expect(screen.getAllByRole('tab')).toHaveLength(5);
    });
  });

  it('marks the matching tab aria-selected=true based on path', async () => {
    renderTabBar('/orders');
    await waitFor(() => {
      const ordersTab = screen.getByRole('tab', { name: /orders/i });
      expect(ordersTab).toHaveAttribute('aria-selected', 'true');
    });
    const overviewTab = screen.getByRole('tab', { name: /overview/i });
    const positionsTab = screen.getByRole('tab', { name: /positions/i });
    const watchlistTab = screen.getByRole('tab', { name: /watchlist/i });
    const moreTab = screen.getByRole('tab', { name: /more/i });
    expect(overviewTab).toHaveAttribute('aria-selected', 'false');
    expect(positionsTab).toHaveAttribute('aria-selected', 'false');
    expect(watchlistTab).toHaveAttribute('aria-selected', 'false');
    expect(moreTab).toHaveAttribute('aria-selected', 'false');
  });

  it('navigates on tab click', async () => {
    const user = userEvent.setup();
    renderTabBar('/overview');
    await waitFor(() => {
      const overviewTab = screen.getByRole('tab', { name: /overview/i });
      expect(overviewTab).toHaveAttribute('aria-selected', 'true');
    });
    const positionsTab = screen.getByRole('tab', { name: /positions/i });
    await user.click(positionsTab);
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /positions/i })).toHaveAttribute(
        'aria-selected',
        'true',
      );
    });
    expect(screen.getByRole('tab', { name: /overview/i })).toHaveAttribute(
      'aria-selected',
      'false',
    );
  });

  it('is hidden on desktop', async () => {
    renderTabBar('/overview');
    await waitFor(() => {
      expect(screen.getAllByRole('tab')).toHaveLength(5);
    });
    const nav = screen.getByRole('tablist');
    expect(nav.className).toContain('md:hidden');
  });
});
