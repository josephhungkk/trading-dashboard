import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
  Outlet,
} from '@tanstack/react-router';
import { WatchlistPage } from './WatchlistPage';
import { useModeStore } from '@/stores/global/mode';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';

class ResizeObserverStub {
  observe(): void { /* noop */ }
  unobserve(): void { /* noop */ }
  disconnect(): void { /* noop */ }
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

for (const prop of ['clientHeight', 'clientWidth', 'offsetHeight', 'offsetWidth'] as const) {
  Object.defineProperty(HTMLElement.prototype, prop, {
    configurable: true,
    get() { return prop.includes('Height') ? 400 : 800; },
  });
}

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

function renderPage(): void {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const watchlistRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/watchlist',
    component: WatchlistPage,
  });
  const routeTree = rootRoute.addChildren([watchlistRoute]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ['/watchlist'] }),
  });
  render(<RouterProvider router={router as never} />);
}

describe('WatchlistPage', () => {
  beforeEach(async () => {
    resetServices();
    const { live, paper } = getBothScopes();
    live.suspend();
    paper.suspend();
    useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
    await paper.hydrate(getServices());
  });

  it('renders a pill for each watchlist', async () => {
    renderPage();
    const region = await screen.findByRole('region', { name: /watchlist/i });
    const pills = region.querySelectorAll('button.rounded-full');
    expect(pills.length).toBeGreaterThan(0);
  });

  it('renders a Customize Columns button', async () => {
    renderPage();
    expect(await screen.findByRole('button', { name: /customize columns/i })).toBeInTheDocument();
  });

  it('renders a Symbol column header from the active watchlist columnConfig', async () => {
    renderPage();
    expect(await screen.findByRole('columnheader', { name: 'Symbol' })).toBeInTheDocument();
  });
});
