export interface BotHealthSnapshot {
  bot_id: string;
  snapshot_at: string;
  bot_name: string;
  sharpe_30d: string | null;
  sharpe_7d: string | null;
  max_drawdown: string | null;
  win_rate: string | null;
  total_pnl: string | null;
  trade_count: number | null;
  advisor_veto_accuracy_1h: string | null;
  exposure_utilisation: string | null;
  trend_badge: string;
}

export interface BotHealthSnapshotHistory {
  snapshot_at: string;
  sharpe_30d: string | null;
  sharpe_7d: string | null;
  max_drawdown: string | null;
  trade_count: number | null;
}

export type CorrelationMatrix = Record<string, Record<string, number>>;

export interface ExposureLimit {
  id: number;
  account_id: string;
  limit_type: string;
  instrument_id: number | null;
  sector: string | null;
  max_notional: string;
  currency: string;
  enabled: boolean;
}

export interface GeneratedStrategy {
  id: string;
  created_at: string;
  sandbox_status: string;
  backtest_sharpe: number | null;
  source_code: string;
  error_message: string | null;
  approved_at: string | null;
  rejected_at: string | null;
}
