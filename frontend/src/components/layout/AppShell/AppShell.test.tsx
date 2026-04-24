import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
} from '@tanstack/react-router';
import { AppShell } from './AppShell';
import { useModeStore } from '@/stores/global/mode';
import { useConnectedStore } from '@/stores/global/connected';
import { useCommandsStore } from '@/stores/global/commands';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';
import type { ConnectedStatus } from '@/services/types';

// jsdom doesn't implement ResizeObserver — stub it. react-resizable-panels
// observes its container to compute panel pixel sizes.
class ResizeObserverStub {
  observe(): void {
    /* noop */
  }
  unobserve(): void {
    /* noop */
  }
  disconnect(): void {
    /* noop */
  }
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

// jsdom doesn't implement matchMedia — DataTable inside RightPanel's compact
// feature views reads useMediaQuery for its mobile breakpoint.
function mkMql(matches: boolean, q: string): MediaQueryList {
  return {
    matches,
    media: q,
    onchange: null,
    addListener: () => { /* noop */ },
    removeListener: () => { /* noop */ },
    addEventListener: () => { /* noop */ },
    removeEventListener: () => { /* noop */ },
    dispatchEvent: () => false,
  } as unknown as MediaQueryList;
}
window.matchMedia = (q: string) => mkMql(q.includes('min-width'), q);

function stubJsdomPointer(): void {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  if (typeof proto['hasPointerCapture'] !== 'function') proto['hasPointerCapture'] = () => false;
  if (typeof proto['releasePointerCapture'] !== 'function')
    proto['releasePointerCapture'] = () => {
      /* jsdom stub */
    };
  if (typeof proto['setPointerCapture'] !== 'function')
    proto['setPointerCapture'] = () => {
      /* jsdom stub */
    };
  if (typeof proto['scrollIntoView'] !== 'function')
    proto['scrollIntoView'] = () => {
      /* jsdom stub */
    };
}

const ROUTE_PATHS = [
  '/overview',
  '/orders',
  '/positions',
  '/watchlist',
  '/admin',
  '/settings',
  '/more',
] as const;

const allGreen: ConnectedStatus[] = [
  { broker: 'ibkr', mode: 'live', gatewayId: 'ibkr-live-gw-1', alias: 'IBKR Live Gateway 1', backendOk: true, gatewayOk: true, latencyMs: 120 },
  { broker: 'ibkr', mode: 'paper', gatewayId: 'ibkr-paper-gw-1', alias: 'IBKR Paper Gateway 1', backendOk: true, gatewayOk: true, latencyMs: 140 },
  { broker: 'futu', gatewayId: 'futu-od-1', alias: 'Futu OpenD', backendOk: true, gatewayOk: true, latencyMs: 80 },
  { broker: 'schwab', gatewayId: 'schwab-api-1', alias: 'Schwab API', backendOk: true, gatewayOk: true, latencyMs: 200 },
];

function makeRouter(initialPath: string): ReturnType<typeof createRouter> {
  const rootRoute = createRootRoute({ component: AppShell });
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
    history: createMemoryHistory({ initialEntries: [initialPath] }),
  });
}

function renderShell(initialPath = '/overview'): ReturnType<typeof render> {
  const router = makeRouter(initialPath);
  // Cast: test router isn't part of the typed router tree.
  return render(<RouterProvider router={router as never} />);
}

describe('AppShell', () => {
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
    document.body.removeAttribute('data-mode');
  });

  afterEach(() => {
    document.body.removeAttribute('data-mode');
    useCommandsStore.setState({ open: false, commands: [] });
    vi.restoreAllMocks();
  });

  it('renders topbar, bottom tab bar, and command palette trigger', async () => {
    renderShell();
    // Topbar brand — desktop and mobile branches both mount, so multiple may appear.
    const brands = await screen.findAllByText('Trading Dashboard');
    expect(brands.length).toBeGreaterThanOrEqual(1);
    // BottomTabBar is a tablist labelled "Primary navigation".
    const tablists = screen.getAllByRole('tablist', { name: /primary navigation/i });
    expect(tablists.length).toBeGreaterThanOrEqual(1);
    // CommandPalette trigger lives in Topbar as a button with aria-label "Open command palette".
    const triggers = screen.getAllByRole('button', { name: /open command palette/i });
    expect(triggers.length).toBeGreaterThanOrEqual(1);
  });

  it('sets document.body data-mode to the active mode on mount', async () => {
    renderShell();
    // Effect runs after first render; wait until Topbar has committed.
    await screen.findAllByText('Trading Dashboard');
    expect(document.body.getAttribute('data-mode')).toBe('paper');
  });

  it('clears data-mode on unmount', async () => {
    const { unmount } = renderShell();
    await screen.findAllByText('Trading Dashboard');
    expect(document.body.getAttribute('data-mode')).toBe('paper');
    act(() => {
      unmount();
    });
    expect(document.body.getAttribute('data-mode')).toBeNull();
  });

  it('reflects mode switches on data-mode', async () => {
    renderShell();
    await screen.findAllByText('Trading Dashboard');
    expect(document.body.getAttribute('data-mode')).toBe('paper');
    act(() => {
      useModeStore.setState({ mode: 'live', pendingMode: null, status: 'idle' });
    });
    expect(document.body.getAttribute('data-mode')).toBe('live');
  });
});
