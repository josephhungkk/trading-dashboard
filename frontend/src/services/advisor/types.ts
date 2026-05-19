export const ADVISOR_MODES = ['OFF', 'OBSERVE', 'VETO', 'SHADOW'] as const;
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

export interface AttributionSummary {
  bot_id: string;
  window: string;
  veto_accuracy: number | null;
  approve_accuracy: number | null;
  avg_avoided_loss_quote: string | null;
  avg_missed_gain_quote: string | null;
  complete_count: number;
  partial_count: number;
  pending_count: number;
  bars_unavailable_count: number;
  unresolvable_count: number;
  skipped_count: number;
  generated_at: string;
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
  overridden_by: string | null;
  override_action: 'approve' | 'veto' | null;
  override_reason: string | null;
  overridden_at: string | null;
  attribution_status: 'pending' | 'partial' | 'complete' | 'bars_unavailable' | 'unresolvable';
  outcome_15m_correct: boolean | null;
  outcome_15m_pnl: string | null;
  outcome_1h_correct: boolean | null;
  outcome_1h_pnl: string | null;
  outcome_4h_correct: boolean | null;
  outcome_4h_pnl: string | null;
  outcome_eod_correct: boolean | null;
  outcome_eod_pnl: string | null;
  attribution_computed_at: string | null;
  created_at: string;
}

export interface AdvisorDecisionsPage {
  items: AdvisorDecision[];
  next_cursor: string | null;
}

export interface AdvisorConfigResponse {
  bot_id: string;
  config: AdvisorConfig;
  account_overrides: Record<string, AdvisorConfig>;
}

export interface AccountAdvisorConfigOverride {
  mode?: AdvisorMode;
  capability?: string;
  local_only?: boolean;
  timeout_ms?: number;
  daily_budget_usd?: string;
}

export interface AccountAdvisorConfigUpdate {
  advisor_config_override: AccountAdvisorConfigOverride | null;
}

export interface AdvisorDecisionOverride {
  override_action: 'approve' | 'veto';
  override_reason: string;
}

export interface AdvisorWsFrame {
  v: 1;
  type?: 'decision' | 'heartbeat';
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
