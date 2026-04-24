import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
  Outlet,
} from '@tanstack/react-router';
import { Topbar } from './Topbar';
import { useCommandsStore } from '@/stores/global/commands';
import { useConnectedStore } from '@/stores/global/connected';
import { useModeStore } from '@/stores/global/mode';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';
import type { ConnectedStatus } from '@/services/types';

const ROUTE_PATHS = [
  '/overview',
  '/orders',
  '/positions',
  '/watchlist',
  '/admin',
  '/settings',
] as const;

const ROUTE_LABELS = ['Overview', 'Orders', 'Positions', 'Watchlist', 'Admin', 'Settings'] as const;

const allGreen: ConnectedStatus[] = [
  { broker: 'ibkr', mode: 'live', gatewayId: 'ibkr-live-gw-1', alias: 'IBKR Live Gateway 1', backendOk: true, gatewayOk: true, latencyMs: 120 },
  { broker: 'ibkr', mode: 'paper', gatewayId: 'ibkr-paper-gw-1', alias: 'IBKR Paper Gateway 1', backendOk: true, gatewayOk: true, latencyMs: 140 },
  { broker: 'futu', gatewayId: 'futu-od-1', alias: 'Futu OpenD', backendOk: true, gatewayOk: true, latencyMs: 80 },
  { broker: 'schwab', gatewayId: 'schwab-api-1', alias: 'Schwab API', backendOk: true, gatewayOk: true, latencyMs: 200 },
];

function stubJsdomPointer(): void {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  if (typeof proto['hasPointerCapture'] !== 'function') proto['hasPointerCapture'] = () => false;
  if (typeof proto['releasePointerCapture'] !== 'function') proto['releasePointerCapture'] = () => { /* jsdom stub */ };
  if (typeof proto['setPointerCapture'] !== 'function') proto['setPointerCapture'] = () => { /* jsdom stub */ };
  if (typeof proto['scrollIntoView'] !== 'function') proto['scrollIntoView'] = () => { /* jsdom stub */ };
}

function makeTestRouter(): ReturnType<typeof createRouter> {
  const rootRoute = createRootRoute({
    component: () => (
      <>
        <Topbar />
        <Outlet />
      </>
    ),
  });
  const childRoutes = ROUTE_PATHS.map((path) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path,
      component: () => <div>{path}</div>,
    }),
  );
  const routeTree = rootRoute.addChildren(childRoutes);
  return createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ['/overview'] }),
  });
}

function renderTopbar(): void {
  const router = makeTestRouter();
  // Cast: test router isn't registered in the typed route tree.
  render(<RouterProvider router={router as never} />);
}

describe('Topbar', () => {
  beforeEach(async () => {
    stubJsdomPointer();
    resetServices();
    const { live, paper } = getBothScopes();
    live.suspend();
    paper.suspend();
    useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
    await paper.hydrate(getServices());
    useConnectedStore.setState({ statuses: allGreen });
    useCommandsStore.setState({ open: false, commands: [] });
  });

  afterEach(() => {
    useCommandsStore.setState({ open: false, commands: [] });
  });

  it('renders all 6 nav tabs', async () => {
    renderTopbar();
    const links = await screen.findAllByRole('link');
    expect(links).toHaveLength(ROUTE_LABELS.length);
    for (const label of ROUTE_LABELS) {
      expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
    }
  });

  it('opens palette when Cmd+K button is clicked', async () => {
    const user = userEvent.setup();
    renderTopbar();
    expect(useCommandsStore.getState().open).toBe(false);
    const trigger = await screen.findByRole('button', { name: /open command palette/i });
    await user.click(trigger);
    expect(useCommandsStore.getState().open).toBe(true);
  });

  it('renders ModeToggle, AccountPicker, and ConnectedDropdown', async () => {
    renderTopbar();
    // ModeToggle renders a PAPER badge and a switch in paper mode.
    expect(await screen.findByText('PAPER')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: /mode/i })).toBeInTheDocument();
    // ConnectedDropdown renders a button with aria-label "connection health".
    expect(screen.getByRole('button', { name: /connection health/i })).toBeInTheDocument();
    // AccountPicker shows an account alias from the paper-scope seed, so
    // the empty-state "Select account" text must not appear.
    expect(screen.queryByText(/select account/i)).not.toBeInTheDocument();
  });
});
