export type PutCall = 'C' | 'P';
export type OptionStyle = 'A' | 'E';

export interface OptionChainRow {
  conid: string;
  strike: string;
  put_call: PutCall;
  bid: string;
  ask: string;
  iv: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  open_interest: number;
  volume: number;
  multiplier: number;
  exchange: string;
  style: OptionStyle;
}

export interface OptionChainData {
  calls: OptionChainRow[];
  puts: OptionChainRow[];
  source: string;
  fetched_at_ms: number;
  stale?: boolean;
}

export interface ExerciseCandidate {
  instrument_id: number;
  qty: string;
  expiry: string;
  strike: string;
  put_call: PutCall;
  multiplier: string;
  exchange: string;
  spot_unavailable: boolean;
}

export interface ExerciseElection {
  id: string;
  action: string;
  status: string;
  expiry_date: string;
  broker_ref: string | null;
}
