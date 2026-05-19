export type UniverseType = "schwab_screener" | "watchlist" | "tickers" | "instruments"

export interface UniverseConfig {
  type: UniverseType
  params: Record<string, unknown>
}

export interface SavedScan {
  id: string
  name: string
  universe_config: UniverseConfig
  rule_expr: string
  schedule: string | null
  market_hours_gate: boolean
  exchange: string | null
  llm_depth: "quick" | "deep"
  alert_id: number | null
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface ScanRun {
  id: string
  scan_id: string | null
  universe_snapshot: string[]
  rule_expr: string
  candidate_count: number
  status: "running" | "completed" | "failed"
  started_at: string
  completed_at: string | null
  error: string | null
}

export interface ScanCandidate {
  id: string
  run_id: string
  instrument_id: number | null
  canonical_id: string
  matched_at: string
  indicator_snapshot: Record<string, number | null>
  llm_commentary: string | null
  llm_depth: "quick" | "deep" | null
}

export interface CreateScanPayload {
  name: string
  universe_config: UniverseConfig
  rule_expr: string
  schedule?: string | null
  market_hours_gate?: boolean
  exchange?: string | null
  llm_depth?: "quick" | "deep"
  alert_id?: number | null
  enabled?: boolean
}

export type ScannerWsFrame =
  | { v: 1; type: "run_started"; ts: string; run_id: string }
  | { v: 1; type: "candidate"; ts: string; candidate: ScanCandidate }
  | {
      v: 1
      type: "run_completed"
      ts: string
      run_id: string
      scan_id: string | null
      candidate_count: number
    }
  | { v: 1; type: "commentary_ready"; ts: string; canonical_id: string; commentary: string }
  | { v: 1; type: "heartbeat"; ts: string }
