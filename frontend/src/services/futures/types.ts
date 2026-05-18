export interface FutureContractMonth {
  conid: string;
  root_symbol: string;
  contract_month: string;
  expiry: string;
  exchange: string;
  multiplier: string;
  tick_size: string;
  tick_value: string;
  settlement_type: 'CASH' | 'PHYSICAL';
  first_notice_day: string | null;
  days_to_expiry: number;
}

export interface FutureRollRule {
  id: string;
  account_id: string;
  instrument_id: number;
  days_before: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface FutureRollRuleRequest {
  instrument_id: number;
  days_before: number;
}

export interface FutureSettlementEvent {
  id: string;
  account_id: string;
  instrument_id: number;
  settlement_price: string;
  cash_delta: string | null;
  settlement_type: 'CASH' | 'PHYSICAL';
  broker_event_id: string | null;
  settled_at: string;
}

export interface RollPreviewResponse {
  nonce: string;
  close_conid: string;
  open_conid: string;
  close_symbol: string;
  open_symbol: string;
  expiry: string;
}
