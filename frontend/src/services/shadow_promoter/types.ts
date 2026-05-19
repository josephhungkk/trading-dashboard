export interface ShadowMetrics {
  sharpe: number;
  mar: number;
  max_dd: number;
  win_rate: number;
  avg_trade_pnl: string;
  total_trades: number;
  window_days: number;
}

export interface ShadowVsLive {
  shadow_bot_id: string;
  shadow_metrics: ShadowMetrics;
  live_metrics: ShadowMetrics;
  delta: { sharpe: string; max_dd: string };
  comparison_ready: boolean;
}

export interface ShadowComparisonReport {
  live_bot_id: string;
  shadows: ShadowVsLive[];
  generated_at: string;
}

export interface ShadowWsFrame {
  v: 1;
  type: 'comparison';
  [key: string]: unknown;
}
