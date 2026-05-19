export type BotStatus = 'stopped' | 'starting' | 'running' | 'pausing' | 'paused' | 'error';

export interface Bot {
  id: string;
  name: string;
  strategy_file: string;
  params_json: Record<string, unknown>;
  params_schema_json: Record<string, unknown> | null;
  version: number;
  status: BotStatus;
  error_msg: string | null;
  mode: 'paper' | 'live';
  bar_timeframe: string;
  created_at: string;
  updated_at: string;
}

export interface BotCreate {
  name: string;
  strategy_file: string;
  params_json: Record<string, unknown>;
  bar_timeframe: string;
  mode: 'paper' | 'live';
  account_ids: string[];
}

export interface BotRun {
  id: string;
  bot_id: string;
  version: number;
  started_at: string;
  stopped_at: string | null;
  stop_reason: 'manual' | 'error' | 'daily_loss_cap' | 'kill_switch' | null;
}

export interface BotOrder {
  order_id: string;
  bot_id: string;
  placed_at: string;
  side: string;
  qty: string;
  status: string;
  account_id: string;
}

export interface RiskCaps {
  max_position_size: number | null;
  max_daily_loss: number | null;
  max_open_orders: number | null;
  max_order_size: number | null;
  allowed_asset_classes: string[] | null;
}

export interface StrategyFile {
  filename: string;
  size: number;
  mtime: string;
}

export interface BotStatusFrame {
  type: 'status_change' | 'heartbeat_loss' | 'fill' | 'daily_loss_cap';
  bot_id: string;
  status: BotStatus;
  data: Record<string, unknown>;
}
