export interface FxPair {
  canonical_id: string;
  base_currency: string;
  quote_currency: string;
  pip_size: string;
}

export interface FxQuote {
  id: string;
  broker_quote_id: string;
  bid: string;
  ask: string;
  ttl_seconds: number;
  expires_at: string;
  status: 'pending' | 'accepting' | 'accepted' | 'expired' | 'rejected';
  side: 'BUY' | 'SELL' | null;
  notional: string | null;
  notional_currency: string | null;
  request_id: string;
}

export interface FxQuoteRequest {
  pair: string;
  notional: string;
  notional_currency: 'base' | 'quote';
  account_id: string;
}

export interface FxAcceptRequest {
  account_id: string;
  side: 'BUY' | 'SELL';
  qty: string;
}

export interface FxPosition {
  instrument_id: number;
  canonical_id: string;
  base_currency: string;
  quote_currency: string;
  qty: string;
  avg_cost: string;
  market_value: string;
  unrealised_pnl: string;
}
