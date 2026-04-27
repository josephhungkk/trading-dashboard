import type { Meta, StoryObj } from '@storybook/react-vite';
import { useMemo } from 'react';
import { OrdersPage } from './OrdersPage';
import type { BrokerMaintenance, OrderResponse } from '@/stores/global/orders';

class StoryEventSource {
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;

  constructor(public readonly url: string) {}

  close(): void {
    /* Storybook-only SSE stub. */
  }
}

globalThis.EventSource = StoryEventSource as unknown as typeof EventSource;

const meta = {
  title: 'Features/OrdersPage',
  component: OrdersPage,
  tags: ['autodocs'],
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof OrdersPage>;

export default meta;
type Story = StoryObj<typeof meta>;

function StoryFrame({
  orders,
  killSwitchActive = false,
  brokerMaintenance = null,
}: {
  orders: OrderResponse[];
  killSwitchActive?: boolean;
  brokerMaintenance?: BrokerMaintenance | null;
}): React.JSX.Element {
  const storySnapshot = useMemo(
    () => ({ orders, killSwitchActive, brokerMaintenance }),
    [orders, killSwitchActive, brokerMaintenance],
  );
  return <OrdersPage storySnapshot={storySnapshot} />;
}

function order(overrides: Partial<OrderResponse>): OrderResponse {
  return {
    id: 'ord-100',
    last_event_at: '2026-04-27T08:00:00Z',
    account_id: 'acct-paper-1',
    broker_order_id: 'broker-100',
    symbol: 'AAPL',
    side: 'BUY',
    order_type: 'LIMIT',
    tif: 'DAY',
    qty: '10',
    limit_price: '180',
    stop_price: null,
    status: 'submitted',
    filled_qty: '0',
    avg_fill_price: null,
    notional: '1800',
    created_at: '2026-04-27T08:00:00Z',
    updated_at: '2026-04-27T08:00:00Z',
    submission_state: 'submitted',
    events: [],
    ...overrides,
  };
}

const ACTIVE_ORDERS: OrderResponse[] = [
  order({ id: 'ord-101', symbol: 'AAPL', side: 'BUY', status: 'pending_submit', qty: '50' }),
  order({ id: 'ord-102', symbol: 'MSFT', side: 'SELL', status: 'submitted', qty: '12' }),
  order({ id: 'ord-103', symbol: 'NVDA', side: 'BUY', status: 'partial', qty: '20', filled_qty: '7', avg_fill_price: '881.42' }),
];

const FILLED_HISTORY: OrderResponse[] = [
  order({ id: 'ord-201', symbol: 'KO', status: 'filled', qty: '25', filled_qty: '25', avg_fill_price: '62.14' }),
  order({ id: 'ord-202', symbol: 'TSLA', status: 'cancelled', qty: '6', filled_qty: '0' }),
  order({ id: 'ord-203', symbol: '7203', status: 'rejected', qty: '100', filled_qty: '0' }),
  order({ id: 'ord-204', symbol: 'VOD', status: 'expired', qty: '300', filled_qty: '0' }),
];

const DAILY_MAINTENANCE: BrokerMaintenance = {
  active: true,
  window: 'daily',
  until: '2026-04-27T22:00:00Z',
};

export const Empty: Story = {
  render: () => <StoryFrame orders={[]} />,
};

export const WithActiveOrders: Story = {
  render: () => <StoryFrame orders={ACTIVE_ORDERS} />,
};

export const WithFilledHistory: Story = {
  render: () => <StoryFrame orders={FILLED_HISTORY} />,
};

export const KillSwitchActive: Story = {
  render: () => <StoryFrame orders={ACTIVE_ORDERS} killSwitchActive />,
};

export const MaintenanceWindow: Story = {
  render: () => <StoryFrame orders={ACTIVE_ORDERS} brokerMaintenance={DAILY_MAINTENANCE} />,
};

export const KillSwitchAndMaintenanceBoth: Story = {
  render: () => (
    <StoryFrame
      orders={[...ACTIVE_ORDERS, ...FILLED_HISTORY]}
      killSwitchActive
      brokerMaintenance={DAILY_MAINTENANCE}
    />
  ),
};
