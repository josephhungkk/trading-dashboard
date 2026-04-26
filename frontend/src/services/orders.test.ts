import { afterEach, describe, it, expect, vi } from 'vitest';
import {
  MockOrdersService,
  listOrders,
  type OrderResponse,
} from './orders';
import { MaintenanceError, SidecarUnreachableError } from './errors';
import { ACCOUNTS } from './fixtures';

describe('MockOrdersService', () => {
  const svc = new MockOrdersService();

  it('list(live) returns only orders for live-mode accounts', async () => {
    const orders = await svc.list('live');
    const liveAcctIds = new Set(ACCOUNTS.filter(a => a.mode === 'live').map(a => a.id));
    expect(orders.length).toBeGreaterThan(0);
    expect(orders.every(o => liveAcctIds.has(o.accountId))).toBe(true);
  });

  it('list(paper) returns only orders for paper-mode accounts', async () => {
    const orders = await svc.list('paper');
    const paperAcctIds = new Set(ACCOUNTS.filter(a => a.mode === 'paper').map(a => a.id));
    expect(orders.length).toBeGreaterThan(0);
    expect(orders.every(o => paperAcctIds.has(o.accountId))).toBe(true);
  });

  it('subscribe returns an unsubscribe function', () => {
    const unsub = svc.subscribe('live', () => {
      /* noop */
    });
    expect(typeof unsub).toBe('function');
    expect(() => unsub()).not.toThrow();
  });
});

describe('listOrders (real-API path)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubFetch(status: number, body: unknown): void {
    const response = {
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(body),
    } as unknown as Response;
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(response)));
  }

  it('returns OrderResponse[] on 200', async () => {
    const expected: OrderResponse[] = [
      {
        order_id: '42',
        contract: {
          symbol: 'AAPL',
          exchange: 'NASDAQ',
          currency: 'USD',
          asset_class: 'STOCK',
          conid: '265598',
          local_symbol: 'AAPL',
        },
        side: 'BUY',
        order_type: 'LIMIT',
        quantity: '100',
        limit_price: { value: '150', currency: 'USD' },
        stop_price: { value: '0', currency: 'USD' },
        time_in_force: 'DAY',
        status: 'SUBMITTED',
        quantity_filled: '0',
        avg_fill_price: { value: '0', currency: 'USD' },
        submitted_at: '2026-04-26T12:00:00+00:00',
        updated_at: null,
      },
    ];
    stubFetch(200, expected);

    await expect(listOrders('a-1')).resolves.toEqual(expected);
  });

  it('throws MaintenanceError on 503 broker_maintenance', async () => {
    stubFetch(503, {
      error: 'broker_maintenance',
      window: 'daily',
      until: '2026-04-26T05:50:00+00:00',
    });

    const err = await listOrders('a-1').catch(e => e);
    expect(err).toBeInstanceOf(MaintenanceError);
    expect((err as MaintenanceError).window).toBe('daily');
  });

  it('throws SidecarUnreachableError on 503 sidecar_unreachable', async () => {
    stubFetch(503, {
      error: 'sidecar_unreachable',
      label: 'isa-paper',
    });

    const err = await listOrders('a-1').catch(e => e);
    expect(err).toBeInstanceOf(SidecarUnreachableError);
    expect((err as SidecarUnreachableError).label).toBe('isa-paper');
  });

  it('throws generic Error on 500', async () => {
    stubFetch(500, { error: 'internal' });

    await expect(listOrders('a-1')).rejects.toThrow(/orders 500/);
  });
});
