import * as React from 'react';
import type { OrderBookLevel, OrderBookSnapshot } from '@/services/crypto/types';

interface Props {
  snapshot: OrderBookSnapshot;
  isStale: boolean;
}

function sortBids(a: OrderBookLevel, b: OrderBookLevel): number {
  return Number.parseFloat(b.price) - Number.parseFloat(a.price);
}

function sortAsks(a: OrderBookLevel, b: OrderBookLevel): number {
  return Number.parseFloat(a.price) - Number.parseFloat(b.price);
}

export function OrderBookDisplay({ snapshot, isStale }: Props): React.JSX.Element {
  const bids = [...snapshot.bids].sort(sortBids).slice(0, 10);
  const asks = [...snapshot.asks].sort(sortAsks).slice(0, 10);
  const spread = asks[0] != null && bids[0] != null
    ? (Number.parseFloat(asks[0].price) - Number.parseFloat(bids[0].price)).toFixed(2)
    : '--';

  return (
    <div className="rounded-md border border-border p-3 font-mono text-sm">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="font-sans font-semibold text-foreground">{snapshot.canonical_id}</div>
        {isStale ? (
          <span className="rounded bg-amber-100 px-2 py-0.5 font-sans text-xs font-medium text-amber-800">
            Stale
          </span>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="mb-1 grid grid-cols-2 text-xs font-medium text-muted-foreground">
            <span>Bid</span>
            <span className="text-right">Qty</span>
          </div>
          {bids.map((level) => (
            <div key={`bid-${level.price}`} className="grid grid-cols-2 text-green-600">
              <span>{level.price}</span>
              <span className="text-right">{level.qty}</span>
            </div>
          ))}
        </div>

        <div>
          <div className="mb-1 grid grid-cols-2 text-xs font-medium text-muted-foreground">
            <span>Ask</span>
            <span className="text-right">Qty</span>
          </div>
          {asks.map((level) => (
            <div key={`ask-${level.price}`} className="grid grid-cols-2 text-red-600">
              <span>{level.price}</span>
              <span className="text-right">{level.qty}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-3 border-t border-border pt-2 text-center font-sans text-xs text-muted-foreground">
        Spread: {spread}
      </div>
    </div>
  );
}
