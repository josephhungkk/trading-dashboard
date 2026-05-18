export interface LegRequest {
  instrument_id: number;
  side: 'buy' | 'sell';
  qty: string;
  position_effect: 'OPEN' | 'CLOSE';
  symbol: string;
  exchange: string;
  currency: string;
  expiry: string;
  strike: string;
  put_call: 'C' | 'P';
  ratio?: number;
  limit_price?: string;
}

export interface ComboPreviewRequest {
  account_id: string;
  strategy_type: string;
  underlying_symbol: string;
  underlying_canonical_id: string;
  tif: string;
  legs: LegRequest[];
}

export interface ComboEnvelope {
  net_debit_credit: string;
  kind: 'DEBIT' | 'CREDIT';
  max_loss: string | null;
  max_profit: string | null;
  break_even: string[];
}

export interface PreviewResponse {
  client_combo_id: string;
  strategy_type: string;
  envelope: ComboEnvelope;
  risk_warnings: unknown[];
  risk_blockers: unknown[];
  csrf_nonce: string;
}

export interface ConfirmRequest {
  account_id: string;
  client_combo_id: string;
  legs: LegRequest[];
  underlying_canonical_id: string;
  strategy_type: string;
  underlying_symbol: string;
  tif: string;
  net_debit_credit: string;
  net_debit_credit_kind: string;
}

export interface ComboOrder {
  id: string;
  account_id: string;
  client_combo_id: string;
  strategy_type: string;
  underlying_symbol: string;
  status: string;
  net_debit_credit: string;
  net_debit_credit_kind: string;
  max_loss: string | null;
  max_profit: string | null;
  break_even: string[];
  tif: string;
  broker_combo_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface CombosListResponse {
  items: ComboOrder[];
  has_more: boolean;
}
