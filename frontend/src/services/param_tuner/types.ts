export type SuggestionStatus =
  | 'pending'
  | 'backtesting'
  | 'ranked'
  | 'approved'
  | 'rejected'
  | 'applied'
  | 'failed';

export interface BacktestResultSnapshot {
  sharpe: number | null;
  mar: number | null;
  max_dd: number | null;
  win_rate: number | null;
  avg_trade_pnl: string;
  forced_close_pnl: string;
  total_trades: number;
}

export interface ParamCandidate {
  params: Record<string, unknown>;
  backtest_job_id: string | null;
  backtest_result: BacktestResultSnapshot | null;
  rank: number | null;
  delta_vs_current: Record<string, string>;
}

export interface ParamSuggestion {
  id: string;
  bot_id: string;
  triggered_by: 'scheduled' | 'manual';
  status: SuggestionStatus;
  candidates: ParamCandidate[];
  ai_reasoning: string | null;
  approved_candidate_index: number | null;
  created_at: string;
  updated_at: string;
}

export interface TunerWsFrame {
  v: 1;
  type: 'backtesting' | 'ranked' | 'applied' | 'failed';
  suggestion_id: string;
  candidate_count?: number;
  candidate_index?: number;
  reason?: string;
}
