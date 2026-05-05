export type Mode = 'live' | 'paper';

export type BrokerId = 'ibkr' | 'futu' | 'schwab' | 'alpaca';
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
  // Optional staleness markers populated by RealQuotesService on
  // 'stale' frames from /ws/quotes (Phase 7b.1 G1). Mock service
  // never sets these; consumers should treat them as default-false.
  isStale?: boolean;
  staleSinceMs?: number;
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
  broker: BrokerId;             // 'ibkr' | 'futu' | 'schwab' | 'alpaca'
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

import type { components } from './api-generated';

export type DecimalString = string & { __brand: 'DecimalString' };

export type BrokerMaintenance = components['schemas']['BrokerMaintenance'];

export interface ContractSummary {
  conid: number;
  description: string;
}

export interface PreviewRequest {
  account_id: string;
  conid: string;
  side: 'BUY' | 'SELL';
  order_type: 'MARKET' | 'LIMIT' | 'STOP';
  tif: 'DAY' | 'GTC';
  qty: DecimalString;
  limit_price?: DecimalString | null;
  stop_price?: DecimalString | null;
}

export interface PositionSanityResult {
  current_qty: DecimalString;
  new_qty_after_fill: DecimalString;
  sanity_multiplier: DecimalString;
  status: 'ok' | 'high' | 'extreme';
  requires_extra_attestation: boolean;
}

export interface PreviewResponse {
  nonce: string;
  notional: DecimalString;
  notional_currency: string;
  notional_filled_today: DecimalString;
  daily_notional_cap: DecimalString;
  max_notional_per_order: DecimalString;
  cap_status: 'ok' | 'near' | 'exceeded';
  daily_cap_status: 'ok' | 'near' | 'exceeded';
  position_sanity: PositionSanityResult;
  contract_summary: ContractSummary;
  warnings: string[];
}

export type OrderSubmissionState = 'submitted' | 'pending_unknown' | 'idempotent_retry';

export interface OrderEvent {
  broker_order_id: string;
  client_order_id: string;
  status: 'pending_submit' | 'submitted' | 'partial' | 'filled' | 'cancelled' | 'rejected' | 'expired' | 'inactive';
  filled_qty: DecimalString;
  avg_fill_price: DecimalString;
  broker_event_at: string;
  raw_payload: string;
}

export interface OrderResponse {
  id: string;
  account_id: string;
  broker_order_id: string | null;
  symbol: string;
  side: 'BUY' | 'SELL';
  order_type: 'MARKET' | 'LIMIT' | 'STOP';
  tif: 'DAY' | 'GTC';
  qty: DecimalString;
  limit_price: DecimalString | null;
  stop_price: DecimalString | null;
  status: OrderEvent['status'];
  filled_qty: DecimalString;
  avg_fill_price: DecimalString | null;
  notional: DecimalString;
  created_at: string;
  updated_at: string;
  last_event_at: string | null;
  submission_state: OrderSubmissionState;
  events: OrderEvent[];
}

export interface OrderListResponse {
  orders: OrderResponse[];
  broker_maintenance: BrokerMaintenance;
  kill_switch_active: boolean;
}

export interface PolicyResponse {
  account_id: string;
  max_notional_per_order: DecimalString;
  daily_notional_cap: DecimalString;
  notional_filled_today: DecimalString;
  trade_enabled: boolean;
  simulator_only: boolean;
  position_count: number;
}
