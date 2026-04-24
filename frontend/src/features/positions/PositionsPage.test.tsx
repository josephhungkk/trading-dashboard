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
import { PositionsPage } from './PositionsPage';
import { useModeStore } from '@/stores/global/mode';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';

class ResizeObserverStub {
  observe(): void { /* noop */ }
  unobserve(): void { /* noop */ }
  disconnect(): void { /* noop */ }
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
  configurable: true,
  get() { return 400; },
});
Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
  configurable: true,
  get() { return 800; },
});
Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
  configurable: true,
  get() { return 400; },
});
Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
  configurable: true,
  get() { return 800; },
});

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
  const positionsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/positions',
    component: PositionsPage,
  });
  const routeTree = rootRoute.addChildren([positionsRoute]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ['/positions'] }),
  });
  render(<RouterProvider router={router as never} />);
}

describe('PositionsPage', () => {
  beforeEach(async () => {
    resetServices();
    const { live, paper } = getBothScopes();
    live.suspend();
    paper.suspend();
    useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
    await paper.hydrate(getServices());
  });

  it('renders an account group heading for each paper account', async () => {
    renderPage();
    expect(await screen.findByRole('heading', { level: 3, name: /IBKR Paper 1/ })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 3, name: /Futu Paper 1/ })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 3, name: /Schwab Paper 1/ })).toBeInTheDocument();
  });

  it('renders positions and P&L column header', async () => {
    renderPage();
    expect(await screen.findByText('GOOGL')).toBeInTheDocument();
    expect(screen.getAllByText('P&L (Unreal.)').length).toBeGreaterThan(0);
  });

  it('applies negative tone class for losers (e.g. AMZN pnlUnrealized -150)', async () => {
    renderPage();
    await screen.findByText('GOOGL');
    const negatives = document.querySelectorAll('.text-negative');
    expect(negatives.length).toBeGreaterThan(0);
  });
});
