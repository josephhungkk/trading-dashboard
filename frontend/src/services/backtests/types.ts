export type BacktestStatus = 'queued' | 'running' | 'done' | 'failed';

export interface BacktestJob {
  id: string;
  bot_id: string;
  status: BacktestStatus;
  timeframe: string;
  canonical_id: string;
  start_date: string;
  end_date: string;
  progress_pct: number;
  created_at: string;
  completed_at: string | null;
}

export interface BacktestTrade {
  canonical_id: string;
  side: string;
  qty: number;
  entry_price: number;
  exit_price: number;
  entry_slippage: number;
  exit_slippage: number;
  commission: number;
  pnl: number;
  forced_close: boolean;
  opened_at: string;
  closed_at: string;
}

export interface BacktestReport {
  sharpe: number | null;
  mar: number | null;
  max_drawdown_pct: number;
  total_return_pct: number;
  total_trades: number;
  win_rate: number | null;
  avg_trade_pnl: number | null;
  forced_close_pnl: number;
  pnl_curve: [string, number][];
  drawdown_curve: [string, number][];
  trades: BacktestTrade[];
}

export interface BacktestJobDetail extends BacktestJob {
  report: BacktestReport | null;
  error_msg: string | null;
}

export interface BacktestSubmitConfig {
  canonical_id: string;
  timeframe: string;
  start_date: string;
  end_date: string;
  slippage_bps: number | null;
  slippage_atr_pct: number | null;
  bars_source: 'db' | 'backfill' | 'csv';
}

export type BacktestProgressFrame =
  | { type: 'progress'; pct: number; trades_so_far: number; current_bar_ts: string }
  | { type: 'done'; report: BacktestReport }
  | { type: 'failed'; error_msg: string }
  | { type: 'heartbeat' };
