import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
  Outlet,
} from '@tanstack/react-router';
import { Toaster } from '@/components/primitives/Toast';
import { useToastStore } from '@/hooks/use-toast';
import { cancelOrder, getOrders } from '@/services/orders';
import type { BrokerMaintenance, DecimalString, OrderResponse } from '@/services/types';
import { useOrdersStore } from '@/stores/global/orders';
import { OrdersPage } from './OrdersPage';

vi.mock('@/services/orders', async () => {
  const actual = await vi.importActual<typeof import('@/services/orders')>('@/services/orders');
  return {
    ...actual,
    getOrders: vi.fn(),
    cancelOrder: vi.fn(),
  };
});

vi.mock('@/components/patterns/TradeTicketModal/TradeTicketModal', () => ({
  TradeTicketModal: ({ mode, orderId, symbol }: { mode: string; orderId: string; symbol?: string }) => (
    <div data-testid="trade-ticket-modal" data-mode={mode} data-order-id={orderId}>
      {symbol !== undefined ? <span>{symbol}</span> : null}
    </div>
  ),
}));

class ResizeObserverStub {
  observe(): void { /* noop */ }
  unobserve(): void { /* noop */ }
  disconnect(): void { /* noop */ }
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

class MockEventSource {
  static instances: MockEventSource[] = [];

  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;
  close = vi.fn();

  constructor(public readonly url: string) {
    MockEventSource.instances.push(this);
  }
}

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

const mockGetOrders = vi.mocked(getOrders);
const mockCancelOrder = vi.mocked(cancelOrder);
const originalEventSource = globalThis.EventSource;

function decimal(value: string): DecimalString {
  return value as DecimalString;
}

function maintenance(overrides: Partial<BrokerMaintenance> = {}): BrokerMaintenance {
  return {
    active: false,
    window: null,
    until: null,
    ...overrides,
  };
}

function order(overrides: Partial<OrderResponse> = {}): OrderResponse {
  return {
    id: 'ord-1',
    account_id: 'acct-1',
    broker_order_id: 'broker-1',
    symbol: 'AAPL',
    side: 'BUY',
    order_type: 'LIMIT',
    tif: 'DAY',
    qty: decimal('10'),
    limit_price: decimal('100'),
    stop_price: null,
    status: 'submitted',
    filled_qty: decimal('0'),
    avg_fill_price: null,
    notional: decimal('1000'),
    created_at: '2026-04-27T08:00:00Z',
    updated_at: '2026-04-27T08:00:00Z',
    last_event_at: '2026-04-27T08:00:00Z',
    submission_state: 'submitted',
    events: [],
    ...overrides,
  };
}

function mockList(
  orders: OrderResponse[],
  opts: { killSwitchActive?: boolean; brokerMaintenance?: BrokerMaintenance } = {},
): void {
  mockGetOrders.mockResolvedValue({
    orders,
    brokerMaintenance: opts.brokerMaintenance ?? maintenance(),
    killSwitchActive: opts.killSwitchActive ?? false,
  });
}

function renderPage(): void {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const ordersRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/orders',
    component: OrdersPage,
  });
  const routeTree = rootRoute.addChildren([ordersRoute]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ['/orders'] }),
  });
  render(
    <>
      <RouterProvider router={router as never} />
      <Toaster />
    </>,
  );
}

describe('OrdersPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    vi.useRealTimers();
    MockEventSource.instances = [];
    globalThis.EventSource = MockEventSource as unknown as typeof EventSource;
    useOrdersStore.getState().clear();
    useToastStore.setState({ items: [] });
    mockCancelOrder.mockResolvedValue(undefined);
  });

  afterEach(() => {
    globalThis.EventSource = originalEventSource;
    useOrdersStore.getState().clear();
    useToastStore.setState({ items: [] });
  });

  it('active_orders_table_renders_pending_submitted_partial', async () => {
    mockList([
      order({ id: 'ord-pending', symbol: 'MSFT', status: 'pending_submit' }),
      order({ id: 'ord-submitted', symbol: 'AAPL', status: 'submitted' }),
      order({ id: 'ord-partial', symbol: 'NVDA', status: 'partial', filled_qty: decimal('2') }),
      order({ id: 'ord-filled', symbol: 'KO', status: 'filled', filled_qty: decimal('10') }),
      order({ id: 'ord-cancelled', symbol: 'TSLA', status: 'cancelled' }),
    ]);

    renderPage();

    const active = await screen.findByLabelText('Active orders table');
    expect(within(active).getByText('MSFT')).toBeInTheDocument();
    expect(within(active).getByText('AAPL')).toBeInTheDocument();
    expect(within(active).getByText('NVDA')).toBeInTheDocument();
    expect(within(active).queryByText('KO')).not.toBeInTheDocument();
    expect(within(active).queryByText('TSLA')).not.toBeInTheDocument();
  });

  it('cancel_button_disabled_for_terminal_status', async () => {
    mockList([
      order({ id: 'ord-filled', symbol: 'FILLED', status: 'filled' }),
      order({ id: 'ord-cancelled', symbol: 'CANCELLED', status: 'cancelled' }),
      order({ id: 'ord-rejected', symbol: 'REJECTED', status: 'rejected' }),
      order({ id: 'ord-expired', symbol: 'EXPIRED', status: 'expired' }),
    ]);

    renderPage();

    await screen.findByLabelText('Recent history table');
    expect(screen.getByRole('button', { name: 'Cancel order ord-filled' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel order ord-cancelled' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel order ord-rejected' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel order ord-expired' })).toBeDisabled();
  });

  it('cancel_button_calls_DELETE_then_shows_toast', async () => {
    const user = userEvent.setup();
    mockList([order({ id: 'ord-delete', symbol: 'AAPL', status: 'submitted' })]);

    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Cancel order ord-delete' }));
    expect(await screen.findByText('Cancel order #ord-delete?')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Confirm cancel' }));

    await waitFor(() => expect(mockCancelOrder).toHaveBeenCalledWith('ord-delete'));
    expect(await screen.findByText('Cancel requested')).toBeInTheDocument();
  });

  it('sse_event_updates_row_in_place', async () => {
    mockList([order({ id: 'ord-stream', symbol: 'AAPL', status: 'submitted', filled_qty: decimal('0') })]);

    renderPage();

    expect(await screen.findByText('submitted')).toBeInTheDocument();
    expect(screen.getByText('0')).toBeInTheDocument();

    act(() => {
      MockEventSource.instances[0]?.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            id: 'ord-stream',
            status: 'partial',
            filled_qty: '4',
            last_event_at: '2026-04-27T08:01:00Z',
          }),
        }),
      );
    });

    expect(await screen.findByText('partial')).toBeInTheDocument();
    expect(screen.getByText('4')).toBeInTheDocument();
    expect(mockGetOrders).toHaveBeenCalledTimes(1);
  });

  it('kill_switch_active_renders_red_banner', async () => {
    mockList([], { killSwitchActive: true });

    renderPage();

    const banner = await screen.findByText('Trading paused by operator');
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveClass('bg-destructive/20');
    expect(banner.parentElement).toHaveClass('sticky');
  });

  it('maintenance_active_renders_amber_banner', async () => {
    mockList([], {
      brokerMaintenance: maintenance({
        active: true,
        window: 'daily',
        until: '2026-04-27T22:00:00Z',
      }),
    });

    renderPage();

    const banner = await screen.findByText(/Broker maintenance active/);
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveClass('bg-warning/20');
  });

  it('modify_button_visible_only_on_non_terminal_rows', async () => {
    mockList([
      order({ id: 'ord-submitted', symbol: 'AAPL', status: 'submitted', account_id: 'acct-1' }),
      order({ id: 'ord-filled', symbol: 'MSFT', status: 'filled', account_id: 'acct-1' }),
      order({ id: 'ord-cancelled', symbol: 'TSLA', status: 'cancelled', account_id: 'acct-1' }),
    ]);

    renderPage();

    await screen.findByLabelText('Active orders table');

    expect(screen.getByRole('button', { name: 'Modify order ord-submitted' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Modify order ord-filled' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Modify order ord-cancelled' })).not.toBeInTheDocument();
  });

  it('click_modify_opens_trade_ticket_modal_in_modify_mode', async () => {
    const user = userEvent.setup();
    mockList([
      order({ id: 'ord-mod', symbol: 'GOOGL', status: 'submitted', qty: '5' as DecimalString, limit_price: '150' as DecimalString, account_id: 'acct-42' }),
    ]);

    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Modify order ord-mod' }));

    const modal = await screen.findByTestId('trade-ticket-modal');
    expect(modal).toHaveAttribute('data-mode', 'modify');
    expect(modal).toHaveAttribute('data-order-id', 'ord-mod');
    expect(within(modal).getByText('GOOGL')).toBeInTheDocument();
  });
});
