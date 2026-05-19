export interface EarningsEvent {
  id: string
  instrument_id: number
  canonical_id: string
  announced_at?: string | null
  announced_date: string
  time_of_day?: "before_open" | "after_close" | "during_market" | "unknown" | null
  eps_estimate?: string | null
  eps_actual?: string | null
  revenue_estimate?: string | null
  revenue_actual?: string | null
  source: "nasdaq_api" | "finnhub_api" | "manual"
  source_priority: number
  confirmed: boolean
  captured_at: string
  updated_at: string
}

export interface EarningsHook {
  id: string
  instrument_id: number
  account_id: string
  hook_type: "auto_flat" | "auto_pause_bot"
  minutes_before: number
  enabled: boolean
  created_at: string
}

export interface EarningsHookCreate {
  instrument_id: number
  account_id: string
  hook_type: "auto_flat" | "auto_pause_bot"
  minutes_before: number
}
