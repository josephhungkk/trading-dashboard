import * as React from 'react';
import type { CFDInstrument } from '@/services/cfd/types';

interface Props {
  cfd: CFDInstrument;
}

export function CFDDetailsSection({ cfd }: Props): React.JSX.Element {
  const { meta } = cfd;

  return (
    <div
      className="rounded-md border border-border p-3 space-y-2 text-sm"
      data-testid="cfd-details-section"
    >
      <div className="font-semibold text-foreground">CFD Details</div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        <span className="text-muted-foreground">Underlying type</span>
        <span>{meta.underlying_type ?? '—'}</span>

        <span className="text-muted-foreground">Underlying symbol</span>
        <span className="font-mono">{meta.underlying_symbol ?? '—'}</span>

        <span className="text-muted-foreground">Currency</span>
        <span>{cfd.currency}</span>

        <span className="text-muted-foreground">Exchange</span>
        <span>{meta.exchange ?? 'IBCFD'}</span>

        <span className="text-muted-foreground">Tick size</span>
        <span className="font-mono">{meta.tick_size ?? '—'}</span>

        <span className="text-muted-foreground">Qty step</span>
        <span className="font-mono">{meta.qty_step ?? '1'}</span>

        <span className="text-muted-foreground">Multiplier</span>
        <span className="font-mono">{meta.multiplier ?? '—'}</span>

        <span className="text-muted-foreground">Margin rate</span>
        <span className="font-mono">
          {meta.margin_rate != null ? `${meta.margin_rate}%` : '—'}
        </span>

        <span className="text-muted-foreground">Max leverage</span>
        <span className="font-mono text-amber-600">
          {meta.max_leverage != null ? `${meta.max_leverage}×` : '—'}
        </span>

        <span className="text-muted-foreground">O/N rate (long)</span>
        <span className="font-mono">
          {meta.overnight_rate_long != null ? `${meta.overnight_rate_long}%` : '—'}
        </span>

        <span className="text-muted-foreground">O/N rate (short)</span>
        <span className="font-mono">
          {meta.overnight_rate_short != null ? `${meta.overnight_rate_short}%` : '—'}
        </span>

        {meta.listed_country && (
          <>
            <span className="text-muted-foreground">Listed country</span>
            <span>{meta.listed_country}</span>
          </>
        )}
      </div>

      <p className="text-xs text-amber-600 mt-1">
        CFD — you do not own the underlying asset. Leverage amplifies gains and losses.
      </p>
    </div>
  );
}
