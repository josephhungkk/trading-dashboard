import * as React from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { ColumnCustomizerDialog } from '@/components/patterns/ColumnCustomizerDialog';
import { DataTable } from '@/components/patterns/DataTable';
import { EmptyState } from '@/components/patterns/EmptyState';
import { MobileCardRow } from '@/components/patterns/MobileCardRow';
import { Button } from '@/components/primitives/Button';
import { NumericCell } from '@/components/primitives/NumericCell';
import { useTickingQuotes } from '@/hooks/use-ticking-quotes';
import { cn } from '@/lib/utils';
 
import { useActiveStores } from '@/stores/registry';
 
import type { Quote, WatchlistColumnKey } from '@/services/types';

interface RowShape {
  symbol: string;
  quote: Quote | undefined;
}

const COLUMN_LABELS: Record<WatchlistColumnKey, string> = {
  symbol: 'Symbol',
  description: 'Description',
  last: 'Last',
  change: 'Change',
  changePct: 'Change %',
  bid: 'Bid',
  ask: 'Ask',
  spread: 'Spread',
  spreadPct: 'Spread %',
  volume: 'Volume',
  avgVol30d: 'Avg Vol 30d',
  dayHigh: 'Day High',
  dayLow: 'Day Low',
  open: 'Open',
  prevClose: 'Prev Close',
  fiftyTwoWkHigh: '52W High',
  fiftyTwoWkLow: '52W Low',
  marketCap: 'Market Cap',
  pe: 'P/E',
  eps: 'EPS',
  divYield: 'Div Yield',
  beta: 'Beta',
  sector: 'Sector',
  industry: 'Industry',
  exchange: 'Exchange',
  assetClass: 'Asset Class',
  nextEarningsDate: 'Next Earnings',
  ivRank: 'IV Rank',
  optionsOI: 'Options OI',
  newsCount24h: 'News 24h',
};

/**
 * Watchlist page — active watchlist selector plus a virtualized DataTable whose
 * visible columns are driven by the watchlist's saved `columnConfig`.
 */
export function WatchlistPage(): React.JSX.Element {
  const stores = useActiveStores();
  const watchlists = stores.useWatchlists((s) => s.watchlists);
  const activeWatchlistId = stores.useWatchlists((s) => s.activeWatchlistId);
  const [customizerOpen, setCustomizerOpen] = React.useState(false);

  const activeWatchlist = React.useMemo(
    () => watchlists.find((watchlist) => watchlist.id === activeWatchlistId) ?? null,
    [activeWatchlistId, watchlists],
  );

  const symbols = React.useMemo(
    () => activeWatchlist?.symbolIds ?? [],
    [activeWatchlist],
  );
  const quotes = useTickingQuotes(symbols);

  const rows = React.useMemo<RowShape[]>(
    () => symbols.map((symbol) => ({ symbol, quote: quotes[symbol] })),
    [quotes, symbols],
  );

  const columns = React.useMemo<ColumnDef<RowShape>[]>(
    () => (activeWatchlist?.columnConfig ?? []).map((key) => columnDef(key)),
    [activeWatchlist],
  );

  if (watchlists.length === 0) {
    return (
      <section className="flex h-full min-h-0 flex-col p-4" aria-label="Watchlist">
        <EmptyState title="No watchlists yet" className="flex-1" />
      </section>
    );
  }

  return (
    <section className="flex h-full min-h-0 flex-col gap-3 p-4" aria-label="Watchlist">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {watchlists.map((watchlist) => {
            const isActive = watchlist.id === activeWatchlistId;
            return (
              <button
                key={watchlist.id}
                type="button"
                onClick={() => stores.useWatchlists.getState().setActive(watchlist.id)}
                className={cn(
                  'rounded-full px-3 py-1 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-primary text-primary-fg'
                    : 'bg-muted/10 text-fg-muted hover:bg-muted/20',
                )}
              >
                {watchlist.name}
              </button>
            );
          })}
        </div>
        <Button
          variant="outline"
          onClick={() => setCustomizerOpen(true)}
          disabled={!activeWatchlist}
        >
          Customize Columns
        </Button>
      </header>

      {!activeWatchlistId || !activeWatchlist ? (
        <div className="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-border bg-panel">
          <p className="text-sm text-fg-muted">Select a watchlist above</p>
        </div>
      ) : (
        <div className="min-h-0 flex-1 rounded-lg border border-border bg-panel">
          <DataTable<RowShape>
            columns={columns}
            data={rows}
            rowKey={(row) => row.symbol}
            mobileRow={(row) => (
              <MobileCardRow
                primary={row.symbol}
                secondary={renderChangePct(row.quote)}
                metrics={[
                  {
                    label: 'Last',
                    value: (
                      <NumericCell value={row.quote?.last} format="number" digits={2} />
                    ),
                  },
                  {
                    label: 'Volume',
                    value: (
                      <NumericCell value={row.quote?.volume} format="number" digits={0} />
                    ),
                  },
                ]}
              />
            )}
          />
        </div>
      )}

      <ColumnCustomizerDialog
        open={customizerOpen}
        onOpenChange={setCustomizerOpen}
        selected={activeWatchlist?.columnConfig ?? []}
        onApply={(next) => {
          if (!activeWatchlist) return;
          void stores.useWatchlists.getState().upsert({
            ...activeWatchlist,
            columnConfig: next,
          });
        }}
      />
    </section>
  );
}

function columnDef(key: WatchlistColumnKey): ColumnDef<RowShape> {
  if (key === 'symbol') {
    return {
      id: key,
      header: COLUMN_LABELS[key],
      cell: ({ row }) => <span className="font-mono text-fg">{row.original.symbol}</span>,
    };
  }

  if (key === 'change') {
    return {
      id: key,
      header: COLUMN_LABELS[key],
      cell: ({ row }) => (
        <NumericCell
          value={row.original.quote?.change}
          format="number"
          digits={2}
          emphasis={toneFor(row.original.quote?.change)}
        />
      ),
    };
  }

  if (key === 'changePct') {
    return {
      id: key,
      header: COLUMN_LABELS[key],
      cell: ({ row }) => (
        <NumericCell
          value={row.original.quote?.changePct}
          format="percent"
          digits={2}
          emphasis={toneFor(row.original.quote?.changePct)}
        />
      ),
    };
  }

  if (key === 'nextEarningsDate') {
    return {
      id: key,
      header: COLUMN_LABELS[key],
      cell: ({ row }) => renderText(getTextValue(row.original.quote, key)),
    };
  }

  // MED-7 (option b): description/exchange/assetClass live on Symbol, not Quote — render '—'
  // rather than passing them to getNumericValue (which doesn't accept these keys).
  if (key === 'description' || key === 'exchange' || key === 'assetClass') {
    return {
      id: key,
      header: COLUMN_LABELS[key],
      cell: () => renderText(null),
    };
  }

  if (isTextColumn(key)) {
    return {
      id: key,
      header: COLUMN_LABELS[key],
      cell: ({ row }) => renderText(getTextValue(row.original.quote, key)),
    };
  }

  return {
    id: key,
    header: COLUMN_LABELS[key],
    cell: ({ row }) => (
      <NumericCell
        value={getNumericValue(row.original.quote, key)}
        format={key === 'spreadPct' || key === 'divYield' ? 'percent' : 'number'}
        digits={digitsFor(key)}
      />
    ),
  };
}

// MED-7: description/exchange/assetClass live on Symbol not Quote — excluded from isTextColumn
// so they fall through to the numeric fallback which renders '—' for null via NumericCell.
function isTextColumn(key: WatchlistColumnKey): key is 'sector' | 'industry' {
  return key === 'sector' || key === 'industry';
}

function getTextValue(quote: Quote | undefined, key: WatchlistColumnKey): string | null | undefined {
  if (!quote) return null;
  if (key === 'sector') return quote.sector;
  if (key === 'industry') return quote.industry;
  if (key === 'nextEarningsDate') return quote.nextEarningsDate;
  return null;
}

function getNumericValue(
  quote: Quote | undefined,
  key: Exclude<
    WatchlistColumnKey,
    'symbol' | 'description' | 'change' | 'changePct' | 'sector' | 'industry' | 'exchange' | 'assetClass' | 'nextEarningsDate'
  >,
): number | null | undefined {
  if (!quote) return null;
  return quote[key];
}

function digitsFor(key: WatchlistColumnKey): number {
  if (key === 'volume' || key === 'avgVol30d' || key === 'optionsOI' || key === 'newsCount24h') {
    return 0;
  }
  if (key === 'marketCap') return 0;
  return 2;
}

function toneFor(value: number | null | undefined): 'up' | 'down' | 'neutral' {
  if ((value ?? 0) > 0) return 'up';
  if ((value ?? 0) < 0) return 'down';
  return 'neutral';
}

function renderText(value: string | null | undefined): React.JSX.Element {
  if (!value) return <span className="text-fg-muted">—</span>;
  return <span className="text-fg">{value}</span>;
}

function renderChangePct(quote: Quote | undefined): React.JSX.Element {
  const value = quote?.changePct;
  const tone =
    toneFor(value) === 'up'
      ? 'text-positive'
      : toneFor(value) === 'down'
        ? 'text-negative'
        : 'text-fg-muted';
  return (
    <span className={tone}>
      <NumericCell
        value={value}
        format="percent"
        digits={2}
        emphasis={toneFor(value)}
      />
    </span>
  );
}
