import type { Meta, StoryObj } from '@storybook/react-vite';
import * as React from 'react';
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
  Outlet,
} from '@tanstack/react-router';
import { BottomTabBar } from './BottomTabBar';

function StubPage({ label }: { label: string }): React.JSX.Element {
  return <div className="p-8 text-fg">{label}</div>;
}

const TAB_PATHS = ['/overview', '/orders', '/positions', '/watchlist', '/more'] as const;

function makeRouter(initialPath: string): ReturnType<typeof createRouter> {
  const rootRoute = createRootRoute({
    component: () => (
      <div className="min-h-screen bg-bg">
        <Outlet />
        <BottomTabBar />
      </div>
    ),
  });
  const childRoutes = TAB_PATHS.map((path) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path,
      component: () => <StubPage label={path} />,
    }),
  );
  const routeTree = rootRoute.addChildren(childRoutes);
  return createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [initialPath] }),
  });
}

function RouterHarness({ initialPath }: { initialPath: string }): React.JSX.Element {
  const router = React.useMemo(() => makeRouter(initialPath), [initialPath]);
  // Cast: story-local router isn't part of the registered typed router.
  return <RouterProvider router={router as never} />;
}

const meta = {
  title: 'Patterns/BottomTabBar',
  component: BottomTabBar,
  tags: ['autodocs'],
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof BottomTabBar>;

export default meta;
type Story = StoryObj<typeof meta>;

export const OverviewActive: Story = {
  render: () => <RouterHarness initialPath="/overview" />,
};

export const OrdersActive: Story = {
  render: () => <RouterHarness initialPath="/orders" />,
};

export const PositionsActive: Story = {
  render: () => <RouterHarness initialPath="/positions" />,
};
