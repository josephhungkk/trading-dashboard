export type Mode = 'live' | 'paper';

export type BrokerId = 'ibkr' | 'futu' | 'schwab';
export interface Broker { id: BrokerId; name: string; }

export type AssetClass =
  | 'stock' | 'forex' | 'crypto' | 'futures' | 'options'
  | 'bond'  | 'etf'   | 'cfd'    | 'commodity' | 'index';

export interface Account {
  id: string; broker: BrokerId; mode: Mode; alias: string;
  accountNumber: string; nlv: number;
  nlvAt: Date | null;
  baseCurrency: 'USD' | 'HKD' | 'GBP' | 'JPY' | 'KRW' | 'EUR' | 'CAD';
}

export interface Symbol {
  symbol: string; exchange: string; description: string;
  assetClass: AssetClass; langTag: string;
}

export interface Quote {
  symbol: string;
  last: number; change: number; changePct: number;
  bid: number; ask: number;
  volume: number; dayHigh: number; dayLow: number;
  open: number; prevClose: number;
  fiftyTwoWkHigh: number; fiftyTwoWkLow: number;
  marketCap: number | null; pe: number | null; eps: number | null;
  divYield: number | null; beta: number | null;
  sector: string | null; industry: string | null;
  avgVol30d: number; sharesOutstanding: number | null;
  nextEarningsDate: string | null;
  ivRank: number | null; optionsOI: number | null; newsCount24h: number;
  spread: number; spreadPct: number;
  isDelayed: boolean; asOf: string;
}

export type OrderStatus = 'open' | 'filled' | 'partial' | 'cancelled' | 'rejected' | 'expired';
export type OrderSide = 'buy' | 'sell';
export type OrderType = 'market' | 'limit' | 'stop' | 'stop_limit';

export interface Order {
  id: string; accountId: string; symbol: string;
  side: OrderSide; qty: number; filledQty: number;
  limitPx: number | null; stopPx: number | null;
  orderType: OrderType; status: OrderStatus;
  createdAt: string; updatedAt: string;
}

export interface Position {
  accountId: string; symbol: string;
  qty: number; avgCost: number; marketValue: number;
  pnlUnrealized: number; pnlRealized: number;
  currency: string; asOf: string;
}

export type WatchlistColumnKey =
  | 'symbol' | 'description' | 'last' | 'change' | 'changePct'
  | 'bid' | 'ask' | 'spread' | 'spreadPct' | 'volume' | 'avgVol30d'
  | 'dayHigh' | 'dayLow' | 'open' | 'prevClose'
  | 'fiftyTwoWkHigh' | 'fiftyTwoWkLow'
  | 'marketCap' | 'pe' | 'eps' | 'divYield' | 'beta'
  | 'sector' | 'industry' | 'exchange' | 'assetClass'
  | 'nextEarningsDate' | 'ivRank' | 'optionsOI' | 'newsCount24h';

export interface Watchlist {
  id: string; name: string;
  assetClass: AssetClass | 'mixed';
  symbolIds: string[];
  columnConfig: WatchlistColumnKey[];
}

export interface ConnectedStatus {
  broker: BrokerId;             // 'ibkr' | 'futu' | 'schwab'
  mode?: Mode;                  // 'live' | 'paper' for IBKR; omitted for single-stack brokers
  gatewayId: string;            // unique gateway instance id, e.g. 'ibkr-live-gw-1'
  alias: string;                // human label, e.g. 'IBKR Live Gateway 1'
  backendOk: boolean;           // backend can reach gateway endpoint
  gatewayOk: boolean;           // gateway logged in + streaming
  latencyMs: number | null;     // last ping ms, null if down
}

export type QuoteFeedType = 'realtime' | 'delayed' | 'none';

export interface QuoteFeedStatus {
  assetClass: AssetClass;       // group label
  exchange?: string;            // optional sub-row; when omitted the row lives at asset-class level
  feedType: QuoteFeedType;
  level?: 1 | 2;                // optional — distinguishes Level I / Level II
}

export interface Command {
  id: string; label: string;
  prefix?: '>' | '@' | '/' | '?';
  run: () => void | Promise<void>;
  keywords?: string[];
}
