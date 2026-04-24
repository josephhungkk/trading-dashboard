import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
  Outlet,
} from '@tanstack/react-router';
import { OverviewPage } from './OverviewPage';
import { useModeStore } from '@/stores/global/mode';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';

// jsdom doesn't implement ResizeObserver — some child primitives may observe.
class ResizeObserverStub {
  observe(): void { /* noop */ }
  unobserve(): void { /* noop */ }
  disconnect(): void { /* noop */ }
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

function renderPage(): void {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const overviewRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/overview',
    component: OverviewPage,
  });
  const routeTree = rootRoute.addChildren([overviewRoute]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ['/overview'] }),
  });
  // Cast: the test router isn't registered in the typed route tree.
  render(<RouterProvider router={router as never} />);
}

describe('OverviewPage', () => {
  beforeEach(async () => {
    resetServices();
    const { live, paper } = getBothScopes();
    live.suspend();
    paper.suspend();
    useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
    await paper.hydrate(getServices());
  });

  it('renders all 4 card titles', async () => {
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Portfolio NLV' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Top Positions' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Orders Today' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Watchlist Favorites' })).toBeInTheDocument();
  });

  it('shows at least one position row in Top Positions card', async () => {
    renderPage();
    const topHeading = await screen.findByRole('heading', { name: 'Top Positions' });
    // The card <section> is the heading's parent element.
    const card = topHeading.parentElement;
    expect(card).not.toBeNull();
    if (card) {
      const rows = within(card).getAllByRole('listitem');
      expect(rows.length).toBeGreaterThan(0);
    }
  });

  it('shows a numeric NLV value in Portfolio NLV card', async () => {
    renderPage();
    const nlvHeading = await screen.findByRole('heading', { name: 'Portfolio NLV' });
    const card = nlvHeading.parentElement;
    expect(card).not.toBeNull();
    if (card) {
      // NumericCell renders digits for non-null values; any digit is sufficient
      // evidence a numeric value (not the em-dash placeholder) is present.
      expect(card.textContent ?? '').toMatch(/\d/);
    }
  });
});
