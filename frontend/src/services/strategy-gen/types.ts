export type SandboxStatus = 'pending' | 'validated' | 'rejected' | 'promoted';

export interface GeneratedStrategy {
  id: number;
  name: string;
  source_hash: string;
  llm_model: string;
  sandbox_status: SandboxStatus;
  sandbox_error: string | null;
  backtest_id: string | null;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
}

export interface GeneratedStrategyDetail extends GeneratedStrategy {
  source_code: string;
  generation_prompt: string;
}

export interface GenerateStrategyRequest {
  asset_class: string;
  market_context: string;
  llm_model?: string;
}

export interface ApproveStrategyRequest {
  bot_name: string;
}

export interface GenerateStrategyResponse {
  id: number;
  status: string;
}

export interface ApproveStrategyResponse {
  strategy_id: number;
  bot_id: string;
  status: string;
}

export interface RejectStrategyResponse {
  id: number;
  status: string;
}
