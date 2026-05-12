import * as React from 'react';

import type { AssetClassExposure } from '@/services/portfolio/types';

interface Props {
  exposures: AssetClassExposure[];
  onDrill: (assetClass: string) => void;
}

/** Long - Short, formatted to 2dp. Inputs are decimal strings. */
function netExposure(longStr: string, shortStr: string): string {
  const long = Number(longStr);
  const short = Number(shortStr);
  if (!Number.isFinite(long) || !Number.isFinite(short)) return '—';
  return (long - short).toFixed(2);
}

export function AssetClassExposureList({
  exposures,
  onDrill,
}: Props): React.JSX.Element {
  return (
    <section
      className="rounded-md border border-border bg-panel p-4"
      data-testid="rollup-exposure-list"
    >
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        Exposure by asset class
      </h2>
      {exposures.length === 0 ? (
        <div className="text-sm text-muted-foreground">No exposure</div>
      ) : (
        <ul className="space-y-1">
          {exposures.map((e) => (
            <li key={e.asset_class}>
              <button
                type="button"
                onClick={() => onDrill(e.asset_class)}
                className="flex w-full items-center justify-between rounded-md px-2 py-2 text-left text-sm hover:bg-muted"
                data-testid={`rollup-exposure-row-${e.asset_class}`}
              >
                <span className="font-medium">{e.asset_class}</span>
                <span className="flex items-center gap-3">
                  <span className="tabular-nums text-muted-foreground">
                    {e.pct_of_nlv}%
                  </span>
                  <span className="tabular-nums" data-testid={`rollup-exposure-net-${e.asset_class}`}>
                    {netExposure(e.long_notional_base, e.short_notional_base)}
                  </span>
                  <span aria-hidden className="text-muted-foreground">
                    ›
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
