export const ADVISOR_MODES = ['OFF', 'OBSERVE', 'VETO'] as const;
export type AdvisorMode = (typeof ADVISOR_MODES)[number];

export interface AdvisorConfig {
  mode: AdvisorMode;
  capability: string;
  local_only: boolean;
  timeout_ms: number;
  daily_budget_usd: string;
  max_qps: number;
  auto_pause_threshold: number;
  auto_pause_window_seconds: number;
  min_veto_confidence: number;
}

export type AdvisorVerdict = 'approve' | 'veto' | 'fail_open';
export type AccountGateOutcome = 'approved' | 'warned' | 'blocked' | 'not_evaluated' | 'error';

export interface ContextSummary {
  bar_count: number;
  position_count: number;
  recent_fill_count: number;
  risk_decision_count: number;
  params_hash: string;
  payload_token_estimate: number;
}

export interface AdvisorDecision {
  id: number;
  bot_id: string;
  bot_run_id: string | null;
  account_id: string;
  canonical_id: string;
  intent: Record<string, unknown>;
  context_summary: ContextSummary;
  prompt_version: number;
  verdict: AdvisorVerdict;
  reasoning: string;
  confidence: number | null;
  advice_tags: string[];
  provider: string | null;
  model: string | null;
  fallback_chain: string[];
  latency_ms: number;
  ai_completion_ts: string | null;
  ai_completion_request_id: string | null;
  account_gate_outcome: AccountGateOutcome;
  account_gate_decision_id: number | null;
  effective_mode: AdvisorMode;
  created_at: string;
}

export interface AdvisorDecisionsPage {
  items: AdvisorDecision[];
  next_before: string | null;
}

export interface AdvisorConfigResponse {
  bot_id: string;
  config: AdvisorConfig;
  account_overrides: Record<string, AdvisorConfig>;
}

export interface AdvisorWsFrame {
  v: 1;
  type?: 'decision';
  decision_id: number;
  bot_id: string;
  account_id?: string;
  canonical_id: string;
  side?: string;
  qty?: string;
  verdict: AdvisorVerdict;
  reasoning?: string;
  reasoning_preview?: string;
  confidence?: number | null;
  advice_tags?: string[];
  latency_ms: number;
  mode?: AdvisorMode;
  effective_mode?: AdvisorMode;
  ts?: string;
  created_at?: string;
  provider?: string | null;
  model?: string | null;
}
