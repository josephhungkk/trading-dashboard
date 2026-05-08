import * as React from 'react';
import { NumericCell } from '@/components/primitives/NumericCell';
import { useActiveStores } from '@/stores/registry';
import type { OrderStatus, Position } from '@/services/types';

/**
 * Overview landing page. Four summary cards:
 *   1. Portfolio NLV — sum of marketValue across all positions in the active
 *      mode. (The positions store is already scoped by mode.)
 *   2. Top Positions — top 5 by pnlUnrealized (descending).
 *   3. Orders Today — counts by status.
 *   4. Watchlist Favorites — first 5 symbols from the active (or first)
 *      watchlist.
 */
export function OverviewPage(): React.JSX.Element {
  const { useAccounts, usePositions, useOrders, useWatchlists } = useActiveStores();

  const accounts = useAccounts((s) => s.accounts);
  const selectedAccountId = useAccounts((s) => s.selectedAccountId);
  const positions = usePositions((s) => s.positions);
  const orders = useOrders((s) => s.orders);
  const watchlists = useWatchlists((s) => s.watchlists);
  const activeWatchlistId = useWatchlists((s) => s.activeWatchlistId);

  const selectedAccount = accounts.find((a) => a.id === selectedAccountId) ?? null;
  const baseCurrency = selectedAccount?.baseCurrency ?? 'USD';

  const portfolioNlv = positions.reduce((sum, p) => sum + p.marketValue, 0);

  const topPositions: Position[] = [...positions]
    .sort((a, b) => b.pnlUnrealized - a.pnlUnrealized)
    .slice(0, 5);

  const orderCounts = countOrderStatuses(orders.map((o) => o.status));

  const activeWatchlist =
    watchlists.find((w) => w.id === activeWatchlistId) ?? watchlists[0] ?? null;
  const favoriteSymbols = activeWatchlist?.symbolIds.slice(0, 5) ?? [];

  return (
    <div
      className="grid grid-cols-1 gap-4 p-4 md:grid-cols-2"
      aria-label="Overview"
    >
      {/* Card 1 — Portfolio NLV */}
      <section className="rounded-lg border border-border bg-panel p-4">
        <h3 className="text-sm font-semibold text-fg">Portfolio NLV</h3>
        <p className="mt-1 text-xs text-fg-muted">
          Sum of market value across all positions in the active mode.
        </p>
        <div className="mt-4 text-2xl">
          <NumericCell value={portfolioNlv} format="currency" currency={baseCurrency} />
        </div>
      </section>

      {/* Card 2 — Top Positions */}
      <section className="rounded-lg border border-border bg-panel p-4">
        <h3 className="text-sm font-semibold text-fg">Top Positions</h3>
        <p className="mt-1 text-xs text-fg-muted">Top 5 by unrealized P&amp;L.</p>
        {topPositions.length === 0 ? (
          <p className="mt-4 text-xs text-fg-muted">No positions.</p>
        ) : (
          <ul className="mt-3 flex flex-col divide-y divide-border text-xs">
            {topPositions.map((p) => (
              <li
                key={`${p.accountId}:${p.symbol}`}
                className="flex items-baseline justify-between gap-3 py-2"
              >
                <span className="font-mono text-fg">{p.symbol}</span>
                <span className="text-fg-muted">{formatQty(p.qty)}</span>
                <NumericCell
                  value={p.pnlUnrealized}
                  format="currency"
                  currency={p.currency}
                  emphasis={toneFor(p.pnlUnrealized)}
                />
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Card 3 — Orders Today */}
      <section className="rounded-lg border border-border bg-panel p-4">
        <h3 className="text-sm font-semibold text-fg">Orders Today</h3>
        <p className="mt-1 text-xs text-fg-muted">Counts by status.</p>
        <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
          <StatusRow label="Open" count={orderCounts.open + orderCounts.partial} />
          <StatusRow label="Filled" count={orderCounts.filled} />
          <StatusRow label="Cancelled" count={orderCounts.cancelled} />
          <StatusRow label="Rejected" count={orderCounts.rejected} />
        </dl>
      </section>

      {/* Card 4 — Watchlist Favorites */}
      <section className="rounded-lg border border-border bg-panel p-4">
        <h3 className="text-sm font-semibold text-fg">Watchlist Favorites</h3>
        <p className="mt-1 text-xs text-fg-muted">
          {activeWatchlist
            ? `First 5 symbols from "${activeWatchlist.name}".`
            : 'No watchlist available.'}
        </p>
        {favoriteSymbols.length === 0 ? (
          <p className="mt-4 text-xs text-fg-muted">No symbols.</p>
        ) : (
          <ul className="mt-3 flex flex-col divide-y divide-border text-xs">
            {favoriteSymbols.map((sym) => (
              <li key={sym} className="py-2">
                <span className="font-mono text-fg">{sym}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function StatusRow({ label, count }: { label: string; count: number }): React.JSX.Element {
  return (
    <div className="flex items-baseline justify-between gap-2 rounded-md bg-bg px-3 py-2">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="font-mono tabular-nums text-fg">{count}</dd>
    </div>
  );
}

function formatQty(qty: number): string {
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 4,
  }).format(qty);
}

function toneFor(n: number): 'up' | 'down' | 'neutral' {
  if (n > 0) return 'up';
  if (n < 0) return 'down';
  return 'neutral';
}

interface OrderStatusCounts {
  open: number;
  filled: number;
  partial: number;
  cancelled: number;
  rejected: number;
  expired: number;
}

function countOrderStatuses(statuses: OrderStatus[]): OrderStatusCounts {
  return statuses.reduce<OrderStatusCounts>(
    (acc, s) => ({ ...acc, [s]: (acc[s] ?? 0) + 1 }),
    { open: 0, filled: 0, partial: 0, cancelled: 0, rejected: 0, expired: 0 },
  );
}
