import type { Position, Mode } from './types';
import { POSITIONS, ACCOUNTS } from './fixtures';
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

export interface PositionResponse {
  contract: Contract;
  quantity: string;
  avg_cost: Money;
  market_price: Money;
  market_value: Money;
  unrealized_pnl: Money;
  realized_pnl_today: Money;
  daily_pnl: Money;
}

const money = (value: number | string, currency: string): Money => ({
  value: value.toString(),
  currency,
});

const MOCK_POSITIONS: PositionResponse[] = POSITIONS.map(position => {
  const marketPrice = position.qty === 0 ? 0 : position.marketValue / position.qty;
  return {
    contract: {
      symbol: position.symbol,
      exchange: 'SMART',
      currency: position.currency,
      asset_class: 'STOCK',
      conid: '0',
      local_symbol: position.symbol,
    },
    quantity: position.qty.toString(),
    avg_cost: money(position.avgCost, position.currency),
    market_price: money(marketPrice, position.currency),
    market_value: money(position.marketValue, position.currency),
    unrealized_pnl: money(position.pnlUnrealized, position.currency),
    realized_pnl_today: money(position.pnlRealized, position.currency),
    daily_pnl: money(0, position.currency),
  };
});

const USE_MOCKS = (import.meta.env.VITE_USE_MOCKS as string | undefined) === 'true';

export async function listPositions(accountId: string): Promise<PositionResponse[]> {
  if (USE_MOCKS) return MOCK_POSITIONS;
  const r = await fetch(`/api/accounts/${encodeURIComponent(accountId)}/positions`, { credentials: 'include' });
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
    throw new Error(`positions ${r.status}: ${body.error ?? 'unknown'}`);
  }
  return (await r.json()) as PositionResponse[];
}

export interface PositionsService {
  list(mode: Mode): Promise<Position[]>;
  subscribe(mode: Mode, cb: (positions: Position[]) => void): () => void;
}

export class MockPositionsService implements PositionsService {
  constructor(private readonly fixtures: Position[] = POSITIONS) {}
  async list(mode: Mode): Promise<Position[]> {
    const ids = new Set(ACCOUNTS.filter(a => a.mode === mode).map(a => a.id));
    return this.fixtures.filter(p => ids.has(p.accountId));
  }
  subscribe(mode: Mode, cb: (positions: Position[]) => void): () => void {
    void mode;
    void cb;
    return () => {
      /* no-op until real adapter wires updates */
    };
  }
}
