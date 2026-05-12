import * as React from 'react';

import type { BaseCurrency, RollupLive } from '@/services/portfolio/types';
import { SUPPORTED_BASES } from '@/services/portfolio/types';

interface Props {
  data: RollupLive;
  base: BaseCurrency;
  onBaseChange: (b: BaseCurrency) => void;
  wsConnected: boolean;
}

export function RollupKpiBar({
  data,
  base,
  onBaseChange,
  wsConnected,
}: Props): React.JSX.Element {
  return (
    <header
      className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-panel p-4"
      data-testid="rollup-kpi-bar"
    >
      <div>
        <div className="text-xs uppercase tracking-wide text-muted-foreground">
          Total NLV ({data.base_currency})
        </div>
        <div className="text-3xl font-semibold tabular-nums" data-testid="rollup-total-nlv">
          {data.total_nlv_base}
        </div>
        <div className="mt-1 flex gap-4 text-xs text-muted-foreground">
          <span>
            Realized today:{' '}
            <span className="tabular-nums">{data.total_realized_today_base}</span>
          </span>
          <span>
            Unrealized:{' '}
            <span className="tabular-nums">{data.total_unrealized_base}</span>
          </span>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs ${
            wsConnected
              ? 'bg-green-100 text-green-800'
              : 'bg-amber-100 text-amber-800'
          }`}
          data-testid="rollup-ws-status"
        >
          <span
            className={`h-2 w-2 rounded-full ${
              wsConnected ? 'bg-green-600' : 'bg-amber-600'
            }`}
          />
          {wsConnected ? 'Live' : 'Polling'}
        </span>

        {data.partial && (
          <span
            className="rounded-md bg-amber-100 px-2 py-1 text-xs text-amber-900"
            data-testid="rollup-partial-badge"
          >
            Partial — {(data.fx_stale_accounts ?? []).length} FX stale
          </span>
        )}

        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Base:</span>
          <select
            value={base}
            onChange={(e) => onBaseChange(e.currentTarget.value as BaseCurrency)}
            className="rounded-md border border-border bg-panel p-1 text-sm"
            data-testid="rollup-base-select"
          >
            {SUPPORTED_BASES.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
      </div>
    </header>
  );
}
