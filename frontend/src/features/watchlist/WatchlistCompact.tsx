import * as React from 'react';
import { NumericCell } from '@/components/primitives/NumericCell';
import { useTickingQuotes } from '@/hooks/use-ticking-quotes';
 
import { useActiveStores } from '@/stores/registry';

export function WatchlistCompact(): React.JSX.Element {
  const stores = useActiveStores();
  const watchlists = stores.useWatchlists((s) => s.watchlists);
  const activeWatchlistId = stores.useWatchlists((s) => s.activeWatchlistId);

  const activeWatchlist = React.useMemo(
    () => watchlists.find((watchlist) => watchlist.id === activeWatchlistId) ?? null,
    [activeWatchlistId, watchlists],
  );

  const symbols = activeWatchlist?.symbolIds.slice(0, 10) ?? [];
  const quotes = useTickingQuotes(symbols);

  if (!activeWatchlist || symbols.length === 0) {
    return (
      <section className="flex h-full flex-col gap-2 p-2">
        <h2 className="text-sm font-semibold text-fg">Watchlist</h2>
        <p className="text-xs text-fg-muted">No active watchlist.</p>
      </section>
    );
  }

  return (
    <section className="flex h-full flex-col p-2">
      <header className="border-b border-border p-2">
        <h2 className="text-sm font-semibold text-fg">{activeWatchlist.name}</h2>
      </header>
      <div className="min-h-0 flex-1 overflow-auto">
        {symbols.map((symbol) => {
          const quote = quotes[symbol];
          const tone =
            (quote?.changePct ?? 0) > 0
              ? 'text-positive'
              : (quote?.changePct ?? 0) < 0
                ? 'text-negative'
                : 'text-fg-muted';
          return (
            <div
              key={symbol}
              className="flex items-center justify-between gap-2 border-b border-border p-2 text-xs"
            >
              <span className="font-mono text-fg">{symbol}</span>
              <div className="flex items-center gap-2">
                <NumericCell value={quote?.last} format="number" digits={2} className="text-xs" />
                <span className={tone}>{formatPercent(quote?.changePct)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function formatPercent(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  return new Intl.NumberFormat(undefined, {
    style: 'percent',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}
