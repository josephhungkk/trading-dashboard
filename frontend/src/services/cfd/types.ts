export interface CFDInstrument {
  id: number;
  canonical_id: string;
  display_name: string;
  currency: string;
  primary_exchange: string;
  meta: {
    underlying_type?: string;
    underlying_symbol?: string;
    underlying_conid?: string;
    tick_size?: string;
    qty_step?: string;
    multiplier?: string;
    margin_rate?: string;
    overnight_rate_long?: string;
    overnight_rate_short?: string;
    max_leverage?: string;
    listed_country?: string;
    exchange?: string;
  };
}
