export interface CryptoAsset {
  canonical_id: string;
  base_asset: string;
  quote_asset: string;
  min_qty: string;
  qty_step: string;
  min_notional: string | null;
  available_24h: boolean;
}

export interface OrderBookLevel {
  price: string;
  qty: string;
}

export interface OrderBookSnapshot {
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  canonical_id: string;
  seq?: number;
}
