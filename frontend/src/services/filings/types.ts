export interface Filing {
  id: string
  instrument_id: number | null
  canonical_id: string | null
  source: "sec_edgar" | "hkex_rns"
  form_type: string
  filing_date: string
  title: string
  url: string
  llm_summary: string | null
  captured_at: string
}

export interface FilingsQuery {
  canonical_id?: string
  source?: "sec_edgar" | "hkex_rns"
  limit?: number
  offset?: number
}
