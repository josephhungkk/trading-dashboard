import * as React from 'react';
import { useEffect } from 'react';

import type { BaseCurrency } from '@/services/portfolio/types';
import { useRollupDrill } from '@/services/portfolio/useRollupDrill';

interface Props {
  assetClass: string | null;
  base: BaseCurrency;
  onClose: () => void;
}

/** Per-instrument row tint by risk-gate verdict. */
function verdictBg(verdict: 'ok' | 'warn' | 'block'): string {
  if (verdict === 'block') return 'bg-red-50 hover:bg-red-100';
  if (verdict === 'warn') return 'bg-amber-50 hover:bg-amber-100';
  return 'hover:bg-muted';
}

export function AssetClassDrillDrawer({
  assetClass,
  base,
  onClose,
}: Props): React.JSX.Element | null {
  const drill = useRollupDrill(assetClass, base);

  useEffect(() => {
    if (assetClass === null) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [assetClass, onClose]);

  if (assetClass === null) return null;

  return (
    <aside
      role="dialog"
      aria-modal="true"
      aria-label={`Drill: ${assetClass}`}
      className="fixed inset-y-0 right-0 z-40 w-full max-w-md overflow-y-auto border-l border-border bg-panel p-4 shadow-xl md:w-[28rem]"
      data-testid="rollup-drill-drawer"
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold" data-testid="rollup-drill-title">
          {assetClass}
        </h2>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md px-2 py-1 text-sm hover:bg-muted"
          aria-label="Close drill"
          data-testid="rollup-drill-close"
        >
          ✕
        </button>
      </div>

      {drill.isLoading ? (
        <div className="text-sm text-muted-foreground" data-testid="rollup-drill-loading">
          Loading…
        </div>
      ) : drill.error ? (
        <div className="text-sm text-red-600" data-testid="rollup-drill-error">
          Failed: {drill.error.message}
        </div>
      ) : drill.data ? (
        <div data-testid="rollup-drill-body">
          {drill.data.instruments.length === 0 ? (
            <div className="text-sm text-muted-foreground">No instruments</div>
          ) : (
            <ul className="space-y-1">
              {drill.data.instruments.map((i) => (
                <li
                  key={i.instrument_id}
                  className={`flex items-center justify-between rounded-md px-2 py-1 text-sm ${verdictBg(i.verdict)}`}
                  data-testid={`rollup-drill-row-${i.instrument_id}`}
                >
                  <span>
                    <span className="font-medium">{i.display_name}</span>
                    <span className="ml-2 text-xs text-muted-foreground">
                      {i.exchange}
                    </span>
                  </span>
                  <span className="flex items-center gap-3 tabular-nums">
                    <span className="text-xs text-muted-foreground">
                      {i.pct_of_nlv}%
                    </span>
                    <span>{i.total_qty}</span>
                    <span className="font-semibold">{i.notional_base}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </aside>
  );
}
