import type { Account, Mode } from './types';
import { ACCOUNTS } from './fixtures';
import { MaintenanceError, SidecarUnreachableError } from './errors';
import { safeParseDecimal } from '../lib/decimal';

export interface AccountResponse {
  id: string;
  broker_id: 'ibkr' | 'futu' | 'schwab';
  alias: string | null;
  mode: 'live' | 'paper';
  currency_base: string;
  display_order: number;
  nlv: string | null;
  nlv_currency: string | null;
  nlv_at: string | null;
}

export interface BrokerMaintenance {
  active: boolean;
  window: 'weekend' | 'daily' | null;
  until: string | null;
}

export interface AccountListResponse {
  accounts: AccountResponse[];
  degraded_sidecars: string[];
  broker_maintenance: BrokerMaintenance;
}

export interface DisplayAccountListResponse {
  accounts: Account[];
  brokerMaintenance: BrokerMaintenance;
}

const MOCK_ACCOUNT_LIST: AccountListResponse = {
  accounts: ACCOUNTS.map((account, index) => ({
    id: account.id,
    broker_id: account.broker as AccountResponse['broker_id'],
    alias: account.alias ?? null,
    mode: account.mode,
    currency_base: account.baseCurrency,
    display_order: index,
    nlv: null,
    nlv_currency: null,
    nlv_at: null,
  })),
  degraded_sidecars: [],
  broker_maintenance: {
    active: false,
    window: null,
    until: null,
  },
};

const USE_MOCKS = (import.meta.env.VITE_USE_MOCKS as string | undefined) === 'true';

const KNOWN_CURRENCIES = ['USD', 'HKD', 'GBP', 'JPY', 'KRW', 'EUR', 'CAD'] as const;
type KnownCurrency = (typeof KNOWN_CURRENCIES)[number];

function pickBaseCurrency(r: AccountResponse): KnownCurrency {
  // Spec §7: prefer nlv_currency (authoritative — same RPC that produced NLV),
  // fallback to currency_base (legacy from Phase 4), finally USD.
  const candidates = [r.nlv_currency, r.currency_base, 'USD'];
  for (const c of candidates) {
    if (c && (KNOWN_CURRENCIES as readonly string[]).includes(c)) {
      return c as KnownCurrency;
    }
  }
  return 'USD';
}

export async function listAccounts(): Promise<AccountListResponse> {
  if (USE_MOCKS) return MOCK_ACCOUNT_LIST;
  const r = await fetch('/api/accounts', { credentials: 'include' });
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
    throw new Error(`accounts ${r.status}: ${body.error ?? 'unknown'}`);
  }
  return (await r.json()) as AccountListResponse;
}

export interface AccountsService {
  list(mode: Mode): Promise<DisplayAccountListResponse>;
  subscribe(mode: Mode, cb: (accounts: Account[]) => void): () => void;
}

export class MockAccountsService implements AccountsService {
  constructor(private readonly fixtures: Account[] = ACCOUNTS) {}
  async list(mode: Mode): Promise<DisplayAccountListResponse> {
    return {
      accounts: this.fixtures.filter(a => a.mode === mode),
      brokerMaintenance: MOCK_ACCOUNT_LIST.broker_maintenance,
    };
  }
  subscribe(mode: Mode, cb: (accounts: Account[]) => void): () => void {
    void mode;
    void cb;
    return () => {
      /* no-op until real adapter wires updates */
    };
  }
}

/** Maps the wire-shape AccountResponse (boundary-stripped, currency
 *  may be "") onto the display Account shape the existing stores +
 *  components consume. account_number isn't exposed by the backend
 *  (M22) so we fall back to the UUID prefix; nlv requires the
 *  /summary endpoint. Spec §7 R3: lossy is informational only;
 *  fixed-point NLV values like "0.10000000" are expected to be lossy. */
export function toDisplayAccount(r: AccountResponse): Account {
  return {
    id: r.id,
    broker: r.broker_id,
    mode: r.mode,
    alias: r.alias ?? '',
    accountNumber: r.id.slice(0, 8),
    nlv: safeParseDecimal(r.nlv ?? '0').display,
    nlvAt: r.nlv_at ? new Date(r.nlv_at) : null,
    baseCurrency: pickBaseCurrency(r),
  };
}

export class RealAccountsService implements AccountsService {
  async list(mode: Mode): Promise<DisplayAccountListResponse> {
    const res = await listAccounts();
    return {
      accounts: res.accounts.filter(a => a.mode === mode).map(toDisplayAccount),
      brokerMaintenance: res.broker_maintenance,
    };
  }
  subscribe(mode: Mode, cb: (accounts: Account[]) => void): () => void {
    void mode;
    void cb;
    return () => {
      /* polling/ws subscription is a Phase 5 follow-up */
    };
  }
}

export const realAccountsService = new RealAccountsService();
