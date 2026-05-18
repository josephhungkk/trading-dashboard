export interface BondInstrument {
  id: number;
  canonical_id: string;
  display_name: string;
  currency: string;
  primary_exchange: string;
  meta: {
    isin?: string;
    cusip?: string;
    coupon_rate?: string;
    coupon_frequency?: number;
    maturity_date?: string;
    face_value?: string;
    bond_type?: string;
    credit_rating?: string;
    yield_to_maturity?: string;
    duration?: string;
    settlement_days?: number;
    callable?: boolean;
  };
}

export interface BondAccruedInterest {
  accrued: string;
  as_of: string;
}
