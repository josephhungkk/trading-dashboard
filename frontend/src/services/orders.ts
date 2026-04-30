import type {
  BrokerMaintenance,
  ContractSummary,
  Mode,
  Order,
  OrderListResponse,
  OrderResponse,
  PolicyResponse,
  PreviewRequest,
  PreviewResponse,
} from './types';
import { ACCOUNTS, ORDERS } from './fixtures';

export interface PlaceResult {
  order: OrderResponse;
  submissionState: OrderResponse['submission_state'];
}

export interface OrderListResult {
  orders: OrderResponse[];
  brokerMaintenance: BrokerMaintenance;
  killSwitchActive: boolean;
}

interface LegacyOrderListResult {
  orders: Order[];
  brokerMaintenance?: BrokerMaintenance;
  killSwitchActive?: boolean;
}

export class BrokerMaintenanceError extends Error {
  constructor(
    public readonly retryAfter: string | null,
    public readonly brokerMaintenance: BrokerMaintenance | null,
  ) {
    super(`broker_maintenance retry_after=${retryAfter ?? 'unknown'}`);
    this.name = 'BrokerMaintenanceError';
  }
}

interface ErrorEnvelope {
  error?: string;
  detail?: string;
  message?: string;
  broker_maintenance?: BrokerMaintenance;
}

interface ContractSearchResponse {
  contracts: ContractSummary[];
}

export interface SearchContractsOptions {
  signal?: AbortSignal;
  broker?: 'ibkr' | 'futu';
}

const USE_MOCKS = (import.meta.env.VITE_USE_MOCKS as string | undefined) === 'true';

const DEFAULT_MAINTENANCE: BrokerMaintenance = {
  active: false,
  window: null,
  until: null,
};

const asDecimal = (value: string): OrderResponse['qty'] => value as OrderResponse['qty'];

const MOCK_ORDER_RESPONSES: OrderResponse[] = ORDERS.map(order => ({
  id: order.id,
  account_id: order.accountId,
  broker_order_id: null,
  symbol: order.symbol,
  side: order.side === 'buy' ? 'BUY' : 'SELL',
  order_type: order.orderType === 'limit' ? 'LIMIT' : order.orderType === 'stop' ? 'STOP' : 'MARKET',
  tif: 'DAY',
  qty: asDecimal(order.qty.toString()),
  limit_price: order.limitPx === null ? null : asDecimal(order.limitPx.toString()),
  stop_price: order.stopPx === null ? null : asDecimal(order.stopPx.toString()),
  status: order.status === 'open' ? 'submitted' : order.status === 'partial' ? 'partial' : order.status === 'filled' ? 'filled' : order.status === 'cancelled' ? 'cancelled' : 'rejected',
  filled_qty: asDecimal(order.filledQty.toString()),
  avg_fill_price: null,
  notional: asDecimal('0'),
  created_at: order.createdAt,
  updated_at: order.updatedAt,
  last_event_at: null,
  submission_state: 'submitted',
  events: [],
}));

async function parseJson(response: Response): Promise<unknown> {
  return response.json().catch(() => null) as Promise<unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  return isRecord(value);
}

function errorMessage(status: number, body: unknown): string {
  if (isErrorEnvelope(body)) {
    const message = body.error ?? body.detail ?? body.message;
    if (message !== undefined) return `orders ${status}: ${message}`;
  }
  return `orders ${status}: unknown`;
}

function isOrderResponse(value: unknown): value is OrderResponse {
  return isRecord(value) && typeof value.submission_state === 'string';
}

async function readOrThrow<T>(response: Response): Promise<T> {
  const body = await parseJson(response);
  if (response.status === 503 && response.headers.get('Retry-After') !== null) {
    const brokerMaintenance = isErrorEnvelope(body) ? body.broker_maintenance ?? null : null;
    throw new BrokerMaintenanceError(response.headers.get('Retry-After'), brokerMaintenance);
  }
  if (response.status === 409 && isOrderResponse(body) && body.submission_state === 'idempotent_retry') {
    return body as T;
  }
  if (!response.ok) {
    throw new Error(errorMessage(response.status, body));
  }
  return body as T;
}

function jsonHeaders(extra?: HeadersInit): HeadersInit {
  return {
    'Content-Type': 'application/json',
    ...extra,
  };
}

export async function previewOrder(req: PreviewRequest): Promise<PreviewResponse> {
  const response = await fetch('/api/orders/preview', {
    method: 'POST',
    credentials: 'include',
    headers: jsonHeaders(),
    body: JSON.stringify(req),
  });
  return readOrThrow<PreviewResponse>(response);
}

export async function placeOrder(
  req: PreviewRequest,
  nonce: string,
  clientOrderId: string,
): Promise<PlaceResult> {
  const response = await fetch('/api/orders', {
    method: 'POST',
    credentials: 'include',
    headers: jsonHeaders({ 'X-Nonce': nonce }),
    body: JSON.stringify({
      ...req,
      nonce,
      client_order_id: clientOrderId,
    }),
  });
  const order = await readOrThrow<OrderResponse>(response);
  return {
    order,
    submissionState: order.submission_state,
  };
}

export async function cancelOrder(id: string): Promise<void> {
  const response = await fetch(`/api/orders/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    credentials: 'include',
  });
  await readOrThrow<unknown>(response);
}

export async function getOrders(opts: { status?: string } = {}): Promise<OrderListResult | LegacyOrderListResult> {
  if (USE_MOCKS) {
    return {
      orders: MOCK_ORDER_RESPONSES,
      brokerMaintenance: DEFAULT_MAINTENANCE,
      killSwitchActive: false,
    };
  }
  const params = new URLSearchParams();
  if (opts.status !== undefined) params.set('status', opts.status);
  const query = params.toString();
  const response = await fetch(`/api/orders${query ? `?${query}` : ''}`, { credentials: 'include' });
  const body = await readOrThrow<OrderListResponse>(response);
  return {
    orders: body.orders,
    brokerMaintenance: body.broker_maintenance,
    killSwitchActive: body.kill_switch_active,
  };
}

export async function getOrderById(id: string): Promise<OrderResponse> {
  const response = await fetch(`/api/orders/${encodeURIComponent(id)}`, { credentials: 'include' });
  return readOrThrow<OrderResponse>(response);
}

export async function getOrderPolicy(accountId: string): Promise<PolicyResponse> {
  const params = new URLSearchParams({ account_id: accountId });
  const response = await fetch(`/api/orders/policy?${params.toString()}`, { credentials: 'include' });
  return readOrThrow<PolicyResponse>(response);
}

export async function searchContracts(
  q: string,
  assetClass?: string,
  signalOrOptions?: AbortSignal | SearchContractsOptions,
): Promise<ContractSummary[]> {
  const signal = signalOrOptions instanceof AbortSignal ? signalOrOptions : signalOrOptions?.signal;
  const broker = signalOrOptions instanceof AbortSignal ? undefined : signalOrOptions?.broker;
  const params = new URLSearchParams({ q });
  if (assetClass !== undefined && assetClass !== '') params.set('asset_class', assetClass);
  if (broker !== undefined) params.set('broker', broker);
  const init: RequestInit = {
    credentials: 'include',
  };
  if (signal !== undefined) init.signal = signal;
  const response = await fetch(`/api/contracts?${params.toString()}`, init);
  const body = await readOrThrow<ContractSearchResponse>(response);
  return body.contracts;
}

export function createDebouncedSearch(
  delayMs = 300,
): (q: string, assetClass?: string, broker?: 'ibkr' | 'futu') => Promise<ContractSummary[]> {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let controller: AbortController | null = null;

  return (q: string, assetClass?: string, broker?: 'ibkr' | 'futu'): Promise<ContractSummary[]> => {
    if (timer !== null) clearTimeout(timer);
    controller?.abort();
    controller = new AbortController();
    const activeController = controller;

    return new Promise((resolve, reject) => {
      timer = setTimeout(() => {
        timer = null;
        const options: SearchContractsOptions = { signal: activeController.signal };
        if (broker !== undefined) options.broker = broker;
        searchContracts(q, assetClass, options).then(resolve, reject);
      }, delayMs);
    });
  };
}

export async function listOrders(accountId: string): Promise<OrderResponse[]> {
  const response = await fetch(`/api/accounts/${encodeURIComponent(accountId)}/orders`, { credentials: 'include' });
  return readOrThrow<OrderResponse[]>(response);
}

export interface OrdersService {
  list(mode: Mode): Promise<Order[]>;
  subscribe(mode: Mode, cb: (orders: Order[]) => void): () => void;
}

export class MockOrdersService implements OrdersService {
  constructor(private readonly fixtures: Order[] = ORDERS) {}

  async list(mode: Mode): Promise<Order[]> {
    const ids = new Set(ACCOUNTS.filter(account => account.mode === mode).map(account => account.id));
    return this.fixtures.filter(order => ids.has(order.accountId));
  }

  subscribe(mode: Mode, cb: (orders: Order[]) => void): () => void {
    void mode;
    void cb;
    return () => {
      /* no-op until real adapter wires updates */
    };
  }
}
