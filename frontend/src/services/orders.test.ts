import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  BrokerMaintenanceError,
  cancelOrder,
  createDebouncedSearch,
  getOrders,
  placeOrder,
  previewOrder,
} from './orders';
import type { ContractSummary, OrderListResponse, OrderResponse, PreviewRequest, PreviewResponse } from './types';

const previewRequest: PreviewRequest = {
  account_id: '1f4f0064-e257-4c9d-928e-9ca6c0695a57',
  conid: '265598',
  side: 'BUY',
  order_type: 'LIMIT',
  tif: 'DAY',
  qty: '10.00000000' as PreviewRequest['qty'],
  limit_price: '150.00000000' as PreviewRequest['qty'],
  stop_price: null,
};

const previewResponse: PreviewResponse = {
  nonce: 'nonce-1',
  notional: '1500.00000000' as PreviewResponse['notional'],
  notional_currency: 'USD',
  notional_filled_today: '0.00000000' as PreviewResponse['notional'],
  daily_notional_cap: '10000.00000000' as PreviewResponse['notional'],
  max_notional_per_order: '5000.00000000' as PreviewResponse['notional'],
  cap_status: 'ok',
  daily_cap_status: 'ok',
  position_sanity: {
    current_qty: '0.00000000' as PreviewResponse['position_sanity']['current_qty'],
    new_qty_after_fill: '10.00000000' as PreviewResponse['position_sanity']['current_qty'],
    sanity_multiplier: '10.00000000' as PreviewResponse['position_sanity']['current_qty'],
    status: 'high',
    requires_extra_attestation: false,
  },
  contract_summary: {
    conid: 265598,
    description: 'AAPL NASDAQ',
  },
  warnings: [],
};

const orderResponse: OrderResponse = {
  id: '1d1f9256-8d1e-45f6-9c92-f1622bb58db6',
  account_id: previewRequest.account_id,
  broker_order_id: '100001',
  symbol: 'AAPL',
  side: 'BUY',
  order_type: 'LIMIT',
  tif: 'DAY',
  qty: previewRequest.qty,
  limit_price: '150.00000000' as OrderResponse['limit_price'],
  stop_price: null,
  status: 'submitted',
  filled_qty: '0.00000000' as OrderResponse['filled_qty'],
  avg_fill_price: null,
  notional: previewResponse.notional,
  created_at: '2026-04-27T09:00:00Z',
  updated_at: '2026-04-27T09:00:00Z',
  last_event_at: null,
  submission_state: 'submitted',
  events: [],
};

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json', ...init.headers },
    ...init,
  });
}

function stubFetch(response: Response | Promise<Response>): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(() => Promise.resolve(response));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('orders service', () => {
  it('test_preview_order_posts_correct_body', async () => {
    const fetchMock = stubFetch(jsonResponse(previewResponse));

    await expect(previewOrder(previewRequest)).resolves.toEqual(previewResponse);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/orders/preview', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(previewRequest),
    });
  });

  it('test_place_order_uses_caller_supplied_client_order_id', async () => {
    const fetchMock = stubFetch(jsonResponse(orderResponse));
    const clientOrderId = '3e3a35aa-175f-4d62-a3e3-8cf7e618ec7a';

    await placeOrder(previewRequest, 'nonce-1', clientOrderId);

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.headers).toEqual({
      'Content-Type': 'application/json',
      'X-Nonce': 'nonce-1',
    });
    expect(JSON.parse(init?.body as string)).toEqual({
      ...previewRequest,
      nonce: 'nonce-1',
      client_order_id: clientOrderId,
    });
  });

  it('test_cancel_order_posts_delete', async () => {
    const fetchMock = stubFetch(jsonResponse({ status: 'accepted' }, { status: 202 }));

    await cancelOrder('order-1');

    expect(fetchMock).toHaveBeenCalledWith('/api/orders/order-1', {
      method: 'DELETE',
      credentials: 'include',
    });
  });

  it('test_search_contracts_factory_debounces_300ms', async () => {
    vi.useFakeTimers();
    const contracts: ContractSummary[] = [{ conid: 265598, description: 'AAPL NASDAQ' }];
    const fetchMock = stubFetch(jsonResponse({ contracts }));
    const search = createDebouncedSearch();

    void search('A').catch(() => undefined);
    void search('AA').catch(() => undefined);
    void search('AAP').catch(() => undefined);
    void search('AAPL').catch(() => undefined);
    void search('AAPL ').catch(() => undefined);
    const resultPromise = search('AAPL N');

    await vi.advanceTimersByTimeAsync(299);
    expect(fetchMock).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    await expect(resultPromise).resolves.toEqual(contracts);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/contracts/search?q=AAPL+N', {
      credentials: 'include',
      signal: expect.any(AbortSignal) as AbortSignal,
    });
  });

  it('test_search_contracts_aborts_in_flight_on_new_query', async () => {
    vi.useFakeTimers();
    const signals: AbortSignal[] = [];
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      if (init?.signal instanceof AbortSignal) signals.push(init.signal);
      return new Promise<Response>(() => undefined);
    });
    vi.stubGlobal('fetch', fetchMock);
    const search = createDebouncedSearch();

    void search('AAPL').catch(() => undefined);
    await vi.advanceTimersByTimeAsync(300);

    void search('MSFT').catch(() => undefined);

    expect(signals[0]?.aborted).toBe(true);
  });

  it('test_get_orders_maps_broker_maintenance_envelope', async () => {
    const body: OrderListResponse = {
      orders: [orderResponse],
      broker_maintenance: {
        active: true,
        window: 'daily',
        until: '2026-04-27T22:00:00Z',
      },
      kill_switch_active: true,
    };
    stubFetch(jsonResponse(body));

    await expect(getOrders({ status: 'submitted' })).resolves.toEqual({
      orders: body.orders,
      brokerMaintenance: body.broker_maintenance,
      killSwitchActive: true,
    });
  });

  it('test_503_maintenance_throws_typed_error', async () => {
    stubFetch(jsonResponse({
      broker_maintenance: {
        active: true,
        window: 'weekend',
        until: '2026-05-02T03:00:00Z',
      },
    }, {
      status: 503,
      headers: { 'Retry-After': '30' },
    }));

    const err = await getOrders().catch((caught: unknown) => caught);
    expect(err).toBeInstanceOf(BrokerMaintenanceError);
    expect((err as BrokerMaintenanceError).retryAfter).toBe('30');
  });

  it('test_409_idempotent_retry_returns_existing_order', async () => {
    const existing = {
      ...orderResponse,
      submission_state: 'idempotent_retry',
    } satisfies OrderResponse;
    stubFetch(jsonResponse(existing, { status: 409 }));

    await expect(placeOrder(previewRequest, 'nonce-1', 'client-order-id')).resolves.toEqual({
      order: existing,
      submissionState: 'idempotent_retry',
    });
  });
});
