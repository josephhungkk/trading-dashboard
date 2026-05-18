export interface FundInstrument {
  id: number;
  canonical_id: string;
  display_name: string;
  currency: string;
  primary_exchange: string;
  meta: {
    isin?: string;
    cusip?: string;
    fund_family?: string;
    fund_type?: string;
    min_investment?: string;
    min_subsequent?: string;
    settlement_days?: number;
    allows_fractional?: boolean;
    cutoff_time_et?: string;
    expense_ratio?: string;
    nav_currency?: string;
  };
}

export interface FundNavSnapshot {
  nav: string;
  nav_date: string;
  source: string;
  captured_at: string;
}
