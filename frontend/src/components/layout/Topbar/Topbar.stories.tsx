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
import { Topbar } from './Topbar';
import { useConnectedStore } from '@/stores/global/connected';
import type { ConnectedStatus } from '@/services/types';

const ROUTE_PATHS = [
  '/overview',
  '/orders',
  '/positions',
  '/watchlist',
  '/admin',
  '/settings',
] as const;

function StubPage({ label }: { label: string }): React.JSX.Element {
  return <div className="p-8 text-fg">{label}</div>;
}

function makeRouter(initialPath: string): ReturnType<typeof createRouter> {
  const rootRoute = createRootRoute({
    component: () => (
      <div className="min-h-screen bg-bg">
        <Topbar />
        <Outlet />
      </div>
    ),
  });
  const childRoutes = ROUTE_PATHS.map((path) =>
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

const allGreen: ConnectedStatus[] = [
  { broker: 'ibkr', mode: 'live', gatewayId: 'ibkr-live-gw-1', alias: 'IBKR Live Gateway 1', backendOk: true, gatewayOk: true, latencyMs: 120 },
  { broker: 'ibkr', mode: 'paper', gatewayId: 'ibkr-paper-gw-1', alias: 'IBKR Paper Gateway 1', backendOk: true, gatewayOk: true, latencyMs: 140 },
  { broker: 'futu', gatewayId: 'futu-od-1', alias: 'Futu OpenD', backendOk: true, gatewayOk: true, latencyMs: 80 },
  { broker: 'schwab', gatewayId: 'schwab-api-1', alias: 'Schwab API', backendOk: true, gatewayOk: true, latencyMs: 200 },
];

function SeedConnected({ children }: { children: React.ReactNode }): React.JSX.Element {
  React.useEffect(() => {
    useConnectedStore.setState({ statuses: allGreen });
  }, []);
  return <>{children}</>;
}

const meta = {
  title: 'Layout/Topbar',
  component: Topbar,
  tags: ['autodocs'],
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof Topbar>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Desktop: Story = {
  parameters: { viewport: { defaultViewport: 'desktop' } },
  render: () => (
    <SeedConnected>
      <RouterHarness initialPath="/overview" />
    </SeedConnected>
  ),
};

export const MobileTwoRow: Story = {
  parameters: { viewport: { defaultViewport: 'mobile1' } },
  render: () => (
    <SeedConnected>
      <RouterHarness initialPath="/overview" />
    </SeedConnected>
  ),
};
