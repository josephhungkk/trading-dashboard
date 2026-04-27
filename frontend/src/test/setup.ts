import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';
import { ACCOUNTS } from '@/services/fixtures';
import type { AccountListResponse, AccountResponse } from '@/services/accounts';

const nativeFetch = globalThis.fetch;

vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url;
  if (url === '/api/accounts') {
    const body: AccountListResponse = {
      accounts: ACCOUNTS.map((account, index): AccountResponse => ({
        id: account.id,
        broker_id: account.broker,
        alias: account.alias,
        mode: account.mode,
        currency_base: account.baseCurrency,
        display_order: index,
        nlv: account.nlv.toFixed(8),
        nlv_currency: account.baseCurrency,
        nlv_at: account.nlvAt?.toISOString() ?? null,
      })),
      degraded_sidecars: [],
      broker_maintenance: {
        active: false,
        window: null,
        until: null,
      },
    };
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
    } as Response);
  }
  return nativeFetch(input, init);
}));
