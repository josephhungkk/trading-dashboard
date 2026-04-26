import type { Order, Mode } from './types';
import { ORDERS, ACCOUNTS } from './fixtures';
import { MaintenanceError, SidecarUnreachableError } from './errors';

export interface Money {
  value: string;
  currency: string;
}

export interface Contract {
  symbol: string;
  exchange: string;
  currency: string;
  asset_class: 'ASSET_UNSPECIFIED' | 'STOCK' | 'ETF' | 'OPTION' | 'FUTURE' | 'FOREX' | 'CRYPTO' | 'BOND' | 'MUTUAL_FUND' | 'WARRANT';
  conid: string;
  local_symbol: string;
}

export interface OrderResponse {
  order_id: string;
  contract: Contract;
  side: 'SIDE_UNSPECIFIED' | 'BUY' | 'SELL';
  order_type: 'TYPE_UNSPECIFIED' | 'MARKET' | 'LIMIT' | 'STOP' | 'STOP_LIMIT';
  quantity: string;
  limit_price: Money;
  stop_price: Money;
  time_in_force: 'TIF_UNSPECIFIED' | 'DAY' | 'GTC' | 'IOC' | 'FOK';
  status: 'STATUS_UNSPECIFIED' | 'PENDING' | 'SUBMITTED' | 'PARTIAL' | 'FILLED' | 'CANCELLED' | 'REJECTED';
  quantity_filled: string;
  avg_fill_price: Money;
  submitted_at: string | null;
  updated_at: string | null;
}

const DEFAULT_CURRENCY = 'USD';

const money = (value: number | string | null, currency = DEFAULT_CURRENCY): Money => ({
  value: (value ?? 0).toString(),
  currency,
});

const statusMap: Record<Order['status'], OrderResponse['status']> = {
  open: 'SUBMITTED',
  filled: 'FILLED',
  partial: 'PARTIAL',
  cancelled: 'CANCELLED',
  rejected: 'REJECTED',
  expired: 'CANCELLED',
};

const orderTypeMap: Record<Order['orderType'], OrderResponse['order_type']> = {
  market: 'MARKET',
  limit: 'LIMIT',
  stop: 'STOP',
  stop_limit: 'STOP_LIMIT',
};

const sideMap: Record<Order['side'], OrderResponse['side']> = {
  buy: 'BUY',
  sell: 'SELL',
};

const MOCK_ORDERS: OrderResponse[] = ORDERS.map(order => ({
  order_id: order.id,
  contract: {
    symbol: order.symbol,
    exchange: 'SMART',
    currency: DEFAULT_CURRENCY,
    asset_class: 'STOCK',
    conid: '0',
    local_symbol: order.symbol,
  },
  side: sideMap[order.side],
  order_type: orderTypeMap[order.orderType],
  quantity: order.qty.toString(),
  limit_price: money(order.limitPx),
  stop_price: money(order.stopPx),
  time_in_force: 'TIF_UNSPECIFIED',
  status: statusMap[order.status],
  quantity_filled: order.filledQty.toString(),
  avg_fill_price: money(0),
  submitted_at: order.createdAt,
  updated_at: order.updatedAt,
}));

const USE_MOCKS = (import.meta.env.VITE_USE_MOCKS as string | undefined) === 'true';

export async function listOrders(accountId: string): Promise<OrderResponse[]> {
  if (USE_MOCKS) return MOCK_ORDERS;
  const r = await fetch(`/api/accounts/${encodeURIComponent(accountId)}/orders`, { credentials: 'include' });
  if (!r.ok) {
    const body = (await r.json().catch(() => ({ error: 'unknown' }))) as {
      error?: string;
      window?: 'weekend' | 'daily';
      until?: string;
      label?: string;
    };
    if (r.status === 503 && body.error === 'broker_maintenance') {
      throw new MaintenanceError(body.window ?? 'daily', body.until ?? '');
    }
    if (r.status === 503 && body.error === 'sidecar_unreachable') {
      throw new SidecarUnreachableError(body.label ?? '');
    }
    throw new Error(`orders ${r.status}: ${body.error ?? 'unknown'}`);
  }
  return (await r.json()) as OrderResponse[];
}

export interface OrdersService {
  list(mode: Mode): Promise<Order[]>;
  subscribe(mode: Mode, cb: (orders: Order[]) => void): () => void;
}

export class MockOrdersService implements OrdersService {
  constructor(private readonly fixtures: Order[] = ORDERS) {}
  async list(mode: Mode): Promise<Order[]> {
    const ids = new Set(ACCOUNTS.filter(a => a.mode === mode).map(a => a.id));
    return this.fixtures.filter(o => ids.has(o.accountId));
  }
  subscribe(mode: Mode, cb: (orders: Order[]) => void): () => void {
    void mode;
    void cb;
    return () => {
      /* no-op until real adapter wires updates */
    };
  }
}
