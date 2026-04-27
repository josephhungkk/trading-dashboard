import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  MockAccountsService,
  listAccounts,
  toDisplayAccount,
  type AccountListResponse,
  type AccountResponse,
} from './accounts';
import { MaintenanceError, SidecarUnreachableError } from './errors';

const baseResponse: AccountResponse = {
  id: 'a1234567-0000-0000-0000-000000000000',
  broker_id: 'ibkr',
  alias: null,
  mode: 'paper',
  currency_base: 'USD',
  display_order: 0,
  nlv: null,
  nlv_currency: null,
  nlv_at: null,
};

describe('toDisplayAccount', () => {
  it('prefers nlv_currency over currency_base', () => {
    const acct = toDisplayAccount({
      ...baseResponse,
      nlv_currency: 'GBP',
      currency_base: 'USD',
    });
    expect(acct.baseCurrency).toBe('GBP');
  });

  it('falls back to currency_base when nlv_currency is null', () => {
    const acct = toDisplayAccount({
      ...baseResponse,
      nlv_currency: null,
      currency_base: 'HKD',
    });
    expect(acct.baseCurrency).toBe('HKD');
  });

  it('falls back to USD when both are unknown', () => {
    const acct = toDisplayAccount({
      ...baseResponse,
      nlv_currency: null,
      currency_base: 'XYZ',
    });
    expect(acct.baseCurrency).toBe('USD');
  });

  it('produces null nlvAt when nlv_at is null', () => {
    const acct = toDisplayAccount(baseResponse);
    expect(acct.nlvAt).toBeNull();
  });

  it('parses nlv_at ISO-8601 to Date', () => {
    const acct = toDisplayAccount({
      ...baseResponse,
      nlv: '100.00000000',
      nlv_currency: 'USD',
      nlv_at: '2026-04-26T12:00:00Z',
    });
    expect(acct.nlvAt).toEqual(new Date('2026-04-26T12:00:00Z'));
  });

  it('does not branch on lossy flag for fixed-point 8-digit input (R3)', () => {
    const acct = toDisplayAccount({
      ...baseResponse,
      nlv: '0.10000000',
      nlv_currency: 'USD',
      nlv_at: '2026-04-26T12:00:00Z',
    });
    expect(acct.nlv).toBe(0.1);
  });

  it('safeParseDecimal of null nlv produces display 0 (not NaN)', () => {
    const acct = toDisplayAccount(baseResponse);
    expect(acct.nlv).toBe(0);
  });
});

describe('MockAccountsService', () => {
  const svc = new MockAccountsService();

  it('list(live) returns only live-mode accounts', async () => {
    const { accounts } = await svc.list('live');
    expect(accounts.length).toBeGreaterThan(0);
    expect(accounts.every(a => a.mode === 'live')).toBe(true);
  });

  it('list(paper) returns only paper-mode accounts', async () => {
    const { accounts } = await svc.list('paper');
    expect(accounts.length).toBeGreaterThan(0);
    expect(accounts.every(a => a.mode === 'paper')).toBe(true);
  });

  it('live and paper lists are disjoint', async () => {
    const { accounts: live } = await svc.list('live');
    const { accounts: paper } = await svc.list('paper');
    const liveIds = new Set(live.map(a => a.id));
    expect(paper.every(a => !liveIds.has(a.id))).toBe(true);
  });

  it('subscribe returns an unsubscribe function', () => {
    const unsub = svc.subscribe('live', () => {
      /* noop */
    });
    expect(typeof unsub).toBe('function');
    expect(() => unsub()).not.toThrow();
  });
});

describe('listAccounts (real-API path)', () => {
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

  it('returns AccountListResponse on 200', async () => {
    const expected: AccountListResponse = {
      accounts: [
        {
          id: 'a-1',
          broker_id: 'ibkr',
          alias: 'ISA Live',
          mode: 'live',
          currency_base: 'USD',
          display_order: 0,
          nlv: '100.00000000',
          nlv_currency: 'USD',
          nlv_at: '2026-04-26T18:00:00Z',
        },
      ],
      degraded_sidecars: ['normal-paper'],
      broker_maintenance: {
        active: false,
        window: null,
        until: null,
      },
    };
    stubFetch(200, expected);

    await expect(listAccounts()).resolves.toEqual(expected);
  });

  it('throws MaintenanceError on 503 broker_maintenance (weekend)', async () => {
    stubFetch(503, {
      error: 'broker_maintenance',
      window: 'weekend',
      until: '2026-05-02T03:00:00+00:00',
    });

    await expect(listAccounts()).rejects.toMatchObject({
      name: 'MaintenanceError',
      window: 'weekend',
      until: '2026-05-02T03:00:00+00:00',
    });
  });

  it('throws MaintenanceError on 503 broker_maintenance (daily)', async () => {
    stubFetch(503, {
      error: 'broker_maintenance',
      window: 'daily',
      until: '2026-04-26T05:50:00+00:00',
    });

    const err = await listAccounts().catch(e => e);
    expect(err).toBeInstanceOf(MaintenanceError);
    expect((err as MaintenanceError).window).toBe('daily');
  });

  it('throws SidecarUnreachableError on 503 sidecar_unreachable', async () => {
    stubFetch(503, {
      error: 'sidecar_unreachable',
      label: 'isa-live',
    });

    const err = await listAccounts().catch(e => e);
    expect(err).toBeInstanceOf(SidecarUnreachableError);
    expect((err as SidecarUnreachableError).label).toBe('isa-live');
  });

  it('throws generic Error on 500', async () => {
    stubFetch(500, { error: 'internal' });

    await expect(listAccounts()).rejects.toThrow(/accounts 500/);
  });

  it('throws generic Error when body is not parseable JSON', async () => {
    const response = {
      ok: false,
      status: 503,
      json: () => Promise.reject(new Error('bad json')),
    } as unknown as Response;
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(response)));

    await expect(listAccounts()).rejects.toThrow(/accounts 503: unknown/);
  });
});
