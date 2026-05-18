import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { listAssets, subscribeOrderBook } from '@/services/crypto/api';
import type { CryptoAsset, OrderBookLevel, OrderBookSnapshot } from '@/services/crypto/types';
import { useActiveStores } from '@/stores/registry';
import { OrderBookDisplay } from './OrderBookDisplay';

interface BookDelta { side: string; price: string; qty: string; seq: number }

function sortBids(a: OrderBookLevel, b: OrderBookLevel): number {
  return Number.parseFloat(b.price) - Number.parseFloat(a.price);
}

function sortAsks(a: OrderBookLevel, b: OrderBookLevel): number {
  return Number.parseFloat(a.price) - Number.parseFloat(b.price);
}

function applyDeltas(snapshot: OrderBookSnapshot, deltas: BookDelta[]): OrderBookSnapshot {
  const bids = new Map(snapshot.bids.map((level) => [level.price, level.qty]));
  const asks = new Map(snapshot.asks.map((level) => [level.price, level.qty]));
  let seq = snapshot.seq;

  for (const delta of deltas) {
    const book = delta.side.toLowerCase() === 'bid' ? bids : asks;
    if (Number.parseFloat(delta.qty) === 0) book.delete(delta.price);
    else book.set(delta.price, delta.qty);
    seq = delta.seq;
  }

  const next: OrderBookSnapshot = {
    canonical_id: snapshot.canonical_id,
    bids: Array.from(bids, ([price, qty]) => ({ price, qty })).sort(sortBids),
    asks: Array.from(asks, ([price, qty]) => ({ price, qty })).sort(sortAsks),
  };
  if (seq !== undefined) next.seq = seq;
  return next;
}

export function CryptoPage(): React.JSX.Element {
  const accountsStore = useActiveStores().useAccounts;
  const selectedAccountId = accountsStore((s) => s.selectedAccountId);
  const firstAccountId = accountsStore((s) => s.accounts[0]?.id ?? null);
  const accountId = selectedAccountId ?? firstAccountId;
  const [selectedAsset, setSelectedAsset] = React.useState<CryptoAsset | null>(null);
  const [snapshot, setSnapshot] = React.useState<OrderBookSnapshot | null>(null);
  const [now, setNow] = React.useState(() => Date.now());
  const [lastUpdate, setLastUpdate] = React.useState<number>(0);

  const assetsQuery = useQuery({
    queryKey: ['crypto', 'assets', accountId],
    queryFn: () => listAssets(accountId ?? ''),
    enabled: accountId !== null,
    staleTime: 60_000,
  });

  // Derived: only show snapshot when it matches the selected asset
  const displaySnapshot =
    snapshot !== null && selectedAsset !== null && snapshot.canonical_id === selectedAsset.canonical_id
      ? snapshot
      : null;

  React.useEffect(() => {
    if (selectedAsset === null) return undefined;

    return subscribeOrderBook(
      selectedAsset.canonical_id,
      (next) => {
        const ts = Date.now();
        setLastUpdate(ts);
        setNow(ts);
        setSnapshot(next);
      },
      (deltas) => {
        const ts = Date.now();
        setLastUpdate(ts);
        setNow(ts);
        setSnapshot((current) => current === null ? current : applyDeltas(current, deltas));
      },
    );
  }, [selectedAsset]);

  React.useEffect(() => {
    if (selectedAsset === null) return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [selectedAsset]);

  const isStale = lastUpdate > 0 && now - lastUpdate > 5000;

  return (
    <main className="p-4 md:p-6 max-w-6xl mx-auto space-y-6">
      <h1 className="text-xl font-semibold">Crypto</h1>

      <div className="grid gap-4 md:grid-cols-2">
        <section className="rounded-md border border-border">
          <div className="border-b border-border p-3">
            <h2 className="text-sm font-medium">Assets</h2>
          </div>
          <div className="divide-y divide-border">
            {accountId === null ? (
              <p className="p-4 text-sm text-muted-foreground">Select an account to load crypto assets.</p>
            ) : null}
            {assetsQuery.isLoading ? (
              <p className="p-4 text-sm text-muted-foreground">Loading...</p>
            ) : null}
            {assetsQuery.isError ? (
              <p className="p-4 text-sm text-red-600">Failed to load crypto assets.</p>
            ) : null}
            {assetsQuery.data?.map((asset) => (
              <button
                key={asset.canonical_id}
                type="button"
                onClick={() => setSelectedAsset(asset)}
                className={[
                  'w-full px-4 py-3 text-left text-sm transition-colors hover:bg-accent',
                  selectedAsset?.canonical_id === asset.canonical_id ? 'bg-accent' : '',
                ].join(' ')}
              >
                <span className="font-medium">{asset.base_asset}/{asset.quote_asset}</span>
                <span className="ml-2 text-xs text-muted-foreground">{asset.canonical_id}</span>
              </button>
            ))}
          </div>
        </section>

        <section className="rounded-md border border-border p-3">
          {selectedAsset === null ? (
            <p className="text-sm text-muted-foreground">Select an asset to view order book</p>
          ) : displaySnapshot === null ? (
            <p className="text-sm text-muted-foreground">Loading order book...</p>
          ) : (
            <OrderBookDisplay snapshot={displaySnapshot} isStale={isStale} />
          )}
        </section>
      </div>
    </main>
  );
}
