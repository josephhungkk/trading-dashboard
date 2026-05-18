import * as React from 'react';
import type { CryptoAsset } from '@/services/crypto/types';

interface Props {
  asset: CryptoAsset;
  lastPrice?: string | null;
}

export function CryptoDetailsSection({ asset, lastPrice = null }: Props): React.JSX.Element {
  return (
    <div className="rounded-md border border-border p-3 space-y-2 text-sm" data-testid="crypto-details-section">
      <div className="font-semibold text-foreground">Crypto Asset</div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        <span className="text-muted-foreground">Pair</span>
        <span className="font-mono">{asset.base_asset}/{asset.quote_asset}</span>

        <span className="text-muted-foreground">Min qty</span>
        <span className="font-mono">{asset.min_qty}</span>

        <span className="text-muted-foreground">Step</span>
        <span className="font-mono">{asset.qty_step} step</span>

        {asset.min_notional != null && (
          <>
            <span className="text-muted-foreground">Min notional</span>
            <span className="font-mono">{asset.min_notional}</span>
          </>
        )}

        {lastPrice != null && (
          <>
            <span className="text-muted-foreground">Last price</span>
            <span className="font-mono">{lastPrice}</span>
          </>
        )}

        <span className="text-muted-foreground">24h trading</span>
        <span className={asset.available_24h ? 'text-green-600' : 'text-muted-foreground'}>
          {asset.available_24h ? 'Available' : 'Unavailable'}
        </span>
      </div>
    </div>
  );
}
