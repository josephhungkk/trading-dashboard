import * as React from 'react';
import { useNavigate, useSearch } from '@tanstack/react-router';

import { SizingMethodColumn } from '@/features/sizing/SizingMethodColumn';
import { Route } from '@/routes/trade.sizing';
import type { SizingMethod } from '@/services/sizing/types';

const METHODS: SizingMethod[] = [
  'fixed_fractional',
  'risk_per_trade',
  'vol_targeted',
];

export function SizingCalculatorPage(): React.JSX.Element {
  const search = useSearch({ from: Route.id });
  const navigate = useNavigate({ from: Route.id });

  type SearchShape = typeof search;
  const updateSearch = (next: Partial<SearchShape>): void => {
    void navigate({
      search: (prev) => ({ ...prev, ...next }) as SearchShape,
    });
  };

  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold">Position sizing</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Compare three sizing methods side-by-side. Inputs persist in the URL.
      </p>

      <section className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
        <input
          placeholder="Account ID (UUID)"
          value={search.account_id ?? ''}
          onChange={(e) =>
            updateSearch({
              account_id: e.currentTarget.value || undefined,
            })
          }
          className="rounded-md border border-border bg-panel p-2 text-sm"
          data-testid="page-account-id"
        />
        <input
          placeholder="Instrument ID (BIGINT)"
          value={search.instrument_id?.toString() ?? ''}
          onChange={(e) => {
            const raw = e.currentTarget.value.trim();
            if (raw === '') {
              updateSearch({ instrument_id: undefined });
              return;
            }
            const parsed = Number.parseInt(raw, 10);
            updateSearch({
              instrument_id: Number.isFinite(parsed) ? parsed : undefined,
            });
          }}
          className="rounded-md border border-border bg-panel p-2 text-sm"
          data-testid="page-instrument-id"
        />
        <select
          value={search.side}
          onChange={(e) =>
            updateSearch({ side: e.currentTarget.value as 'buy' | 'sell' })
          }
          className="rounded-md border border-border bg-panel p-2 text-sm"
          data-testid="page-side"
        >
          <option value="buy">Buy</option>
          <option value="sell">Sell</option>
        </select>
        <input
          placeholder="Entry price"
          value={search.entry ?? ''}
          onChange={(e) =>
            updateSearch({ entry: e.currentTarget.value || undefined })
          }
          className="rounded-md border border-border bg-panel p-2 text-sm"
          data-testid="page-entry"
        />
      </section>

      <section className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {METHODS.map((m) => (
          <SizingMethodColumn
            key={m}
            method={m}
            accountId={search.account_id}
            instrumentId={search.instrument_id}
            side={search.side}
            entry={search.entry}
            stop={search.stop}
          />
        ))}
      </section>
    </div>
  );
}
