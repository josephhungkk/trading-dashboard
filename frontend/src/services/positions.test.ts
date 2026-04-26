import { afterEach, describe, it, expect, vi } from 'vitest';
import {
  MockPositionsService,
  listPositions,
  type PositionResponse,
} from './positions';
import { MaintenanceError, SidecarUnreachableError } from './errors';
import { ACCOUNTS } from './fixtures';

describe('MockPositionsService', () => {
  const svc = new MockPositionsService();

  it('list(live) returns only positions for live-mode accounts', async () => {
    const positions = await svc.list('live');
    const liveAcctIds = new Set(ACCOUNTS.filter(a => a.mode === 'live').map(a => a.id));
    expect(positions.length).toBeGreaterThan(0);
    expect(positions.every(p => liveAcctIds.has(p.accountId))).toBe(true);
  });

  it('list(paper) returns only positions for paper-mode accounts', async () => {
    const positions = await svc.list('paper');
    const paperAcctIds = new Set(ACCOUNTS.filter(a => a.mode === 'paper').map(a => a.id));
    expect(positions.length).toBeGreaterThan(0);
    expect(positions.every(p => paperAcctIds.has(p.accountId))).toBe(true);
  });

  it('subscribe returns an unsubscribe function', () => {
    const unsub = svc.subscribe('live', () => {
      /* noop */
    });
    expect(typeof unsub).toBe('function');
    expect(() => unsub()).not.toThrow();
  });
});

describe('listPositions (real-API path)', () => {
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

  it('returns PositionResponse[] on 200', async () => {
    const expected: PositionResponse[] = [
      {
        contract: {
          symbol: 'AAPL',
          exchange: 'NASDAQ',
          currency: 'USD',
          asset_class: 'STOCK',
          conid: '265598',
          local_symbol: 'AAPL',
        },
        quantity: '100',
        avg_cost: { value: '150', currency: 'USD' },
        market_price: { value: '160', currency: 'USD' },
        market_value: { value: '16000', currency: 'USD' },
        unrealized_pnl: { value: '1000', currency: 'USD' },
        realized_pnl_today: { value: '0', currency: 'USD' },
        daily_pnl: { value: '50', currency: 'USD' },
      },
    ];
    stubFetch(200, expected);

    await expect(listPositions('a-1')).resolves.toEqual(expected);
  });

  it('throws MaintenanceError on 503 broker_maintenance', async () => {
    stubFetch(503, {
      error: 'broker_maintenance',
      window: 'weekend',
      until: '2026-05-02T03:00:00+00:00',
    });

    const err = await listPositions('a-1').catch(e => e);
    expect(err).toBeInstanceOf(MaintenanceError);
    expect((err as MaintenanceError).window).toBe('weekend');
  });

  it('throws SidecarUnreachableError on 503 sidecar_unreachable', async () => {
    stubFetch(503, {
      error: 'sidecar_unreachable',
      label: 'normal-paper',
    });

    const err = await listPositions('a-1').catch(e => e);
    expect(err).toBeInstanceOf(SidecarUnreachableError);
    expect((err as SidecarUnreachableError).label).toBe('normal-paper');
  });

  it('throws generic Error on 500', async () => {
    stubFetch(500, { error: 'internal' });

    await expect(listPositions('a-1')).rejects.toThrow(/positions 500/);
  });
});
