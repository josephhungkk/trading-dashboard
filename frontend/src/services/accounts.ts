import type { Account, Mode } from './types';
import { ACCOUNTS } from './fixtures';
import { MaintenanceError, SidecarUnreachableError } from './errors';

export interface AccountResponse {
  id: string;
  broker_id: 'ibkr' | 'futu' | 'schwab';
  alias: string | null;
  mode: 'live' | 'paper';
  currency_base: string;
  display_order: number;
}

export interface AccountListResponse {
  accounts: AccountResponse[];
  degraded_sidecars: string[];
}

const MOCK_ACCOUNT_LIST: AccountListResponse = {
  accounts: ACCOUNTS.map((account, index) => ({
    id: account.id,
    broker_id: account.broker as AccountResponse['broker_id'],
    alias: account.alias ?? null,
    mode: account.mode,
    currency_base: account.baseCurrency,
    display_order: index,
  })),
  degraded_sidecars: [],
};

const USE_MOCKS = (import.meta.env.VITE_USE_MOCKS as string | undefined) === 'true';

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
  list(mode: Mode): Promise<Account[]>;
  subscribe(mode: Mode, cb: (accounts: Account[]) => void): () => void;
}

export class MockAccountsService implements AccountsService {
  constructor(private readonly fixtures: Account[] = ACCOUNTS) {}
  async list(mode: Mode): Promise<Account[]> {
    return this.fixtures.filter(a => a.mode === mode);
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
 *  /summary endpoint and is filled in lazily by the positions/account
 *  detail flows — placeholder 0 here so the picker can render. */
function toDisplayAccount(r: AccountResponse): Account {
  type DisplayCurrency = Account['baseCurrency'];
  const allowed: DisplayCurrency[] = ['USD', 'HKD', 'GBP', 'JPY', 'KRW'];
  const baseCurrency: DisplayCurrency =
    (allowed as readonly string[]).includes(r.currency_base)
      ? (r.currency_base as DisplayCurrency)
      : 'USD';
  return {
    id: r.id,
    broker: r.broker_id,
    mode: r.mode,
    alias: r.alias ?? '',
    accountNumber: r.id.slice(0, 8),
    nlv: 0,
    baseCurrency,
  };
}

export class RealAccountsService implements AccountsService {
  async list(mode: Mode): Promise<Account[]> {
    const res = await listAccounts();
    return res.accounts.filter(a => a.mode === mode).map(toDisplayAccount);
  }
  subscribe(mode: Mode, cb: (accounts: Account[]) => void): () => void {
    void mode;
    void cb;
    return () => {
      /* polling/ws subscription is a Phase 5 follow-up */
    };
  }
}
