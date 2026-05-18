import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { searchBonds } from '@/services/bonds/api';
import type { BondInstrument } from '@/services/bonds/types';

function BondRow({ bond }: { readonly bond: BondInstrument }): React.JSX.Element {
  const { meta } = bond;
  return (
    <tr className="border-b border-border last:border-0">
      <td className="py-3 pr-4 text-sm font-medium">{bond.display_name ?? bond.canonical_id}</td>
      <td className="py-3 pr-4 text-sm font-mono text-muted-foreground">{meta.isin ?? '—'}</td>
      <td className="py-3 pr-4 text-sm">{meta.bond_type ?? '—'}</td>
      <td className="py-3 pr-4 text-sm font-mono">
        {meta.coupon_rate != null ? `${meta.coupon_rate}%` : '—'}
      </td>
      <td className="py-3 pr-4 text-sm font-mono">{meta.maturity_date ?? '—'}</td>
      <td className="py-3 text-sm">{bond.currency}</td>
    </tr>
  );
}

export function BondsPage(): React.JSX.Element {
  const [query, setQuery] = React.useState('');
  const [debouncedQuery, setDebouncedQuery] = React.useState('');

  React.useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQuery(query), 300);
    return () => window.clearTimeout(t);
  }, [query]);

  const bondsQuery = useQuery({
    queryKey: ['bonds', 'search', debouncedQuery],
    queryFn: () => (debouncedQuery.length >= 1 ? searchBonds(debouncedQuery) : Promise.resolve([])),
    staleTime: 30_000,
  });

  return (
    <main className="max-w-5xl mx-auto p-4 space-y-6" data-testid="bonds-page">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Bonds</h1>
      </div>

      <section className="space-y-3">
        <input
          type="search"
          placeholder="Search by name, ISIN, or CUSIP…"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
          aria-label="Search bonds"
        />

        {bondsQuery.isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {bondsQuery.isError && (
          <p className="text-sm text-red-600">Failed to load bonds.</p>
        )}

        {bondsQuery.data != null && bondsQuery.data.length === 0 && debouncedQuery.length >= 1 && (
          <p className="text-sm text-muted-foreground">No bonds found for &quot;{debouncedQuery}&quot;.</p>
        )}

        {bondsQuery.data != null && bondsQuery.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-border">
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Name</th>
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">ISIN</th>
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Type</th>
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Coupon</th>
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Maturity</th>
                  <th className="pb-2 text-xs font-medium text-muted-foreground">Currency</th>
                </tr>
              </thead>
              <tbody>
                {bondsQuery.data.map((b) => (
                  <BondRow key={b.id} bond={b} />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {debouncedQuery.length === 0 && (
          <p className="text-sm text-muted-foreground">Enter a search term to find bonds.</p>
        )}
      </section>
    </main>
  );
}
