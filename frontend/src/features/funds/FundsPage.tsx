import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { searchFunds } from '@/services/funds/api';
import type { FundInstrument } from '@/services/funds/types';

function FundRow({ fund }: { readonly fund: FundInstrument }): React.JSX.Element {
  const { meta } = fund;
  return (
    <tr className="border-b border-border last:border-0">
      <td className="py-3 pr-4 text-sm font-medium">{fund.display_name ?? fund.canonical_id}</td>
      <td className="py-3 pr-4 text-sm text-muted-foreground">{meta.fund_family ?? '—'}</td>
      <td className="py-3 pr-4 text-sm">{meta.fund_type ?? '—'}</td>
      <td className="py-3 pr-4 text-sm font-mono">{meta.min_investment ?? '—'}</td>
      <td className="py-3 pr-4 text-sm">
        {meta.allows_fractional ? (
          <span className="text-green-600 text-xs">Yes</span>
        ) : (
          <span className="text-muted-foreground text-xs">No</span>
        )}
      </td>
      <td className="py-3 text-sm">{fund.currency}</td>
    </tr>
  );
}

export function FundsPage(): React.JSX.Element {
  const [query, setQuery] = React.useState('');
  const [debouncedQuery, setDebouncedQuery] = React.useState('');

  React.useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQuery(query), 300);
    return () => window.clearTimeout(t);
  }, [query]);

  const fundsQuery = useQuery({
    queryKey: ['funds', 'search', debouncedQuery],
    queryFn: () => (debouncedQuery.length >= 1 ? searchFunds(debouncedQuery) : Promise.resolve([])),
    staleTime: 30_000,
  });

  return (
    <main className="max-w-5xl mx-auto p-4 space-y-6" data-testid="funds-page">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Mutual Funds</h1>
      </div>

      <section className="space-y-3">
        <input
          type="search"
          placeholder="Search by name, ISIN, or CUSIP…"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
          aria-label="Search funds"
        />

        {fundsQuery.isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {fundsQuery.isError && (
          <p className="text-sm text-red-600">Failed to load funds.</p>
        )}

        {fundsQuery.data != null && fundsQuery.data.length === 0 && debouncedQuery.length >= 1 && (
          <p className="text-sm text-muted-foreground">No funds found for &quot;{debouncedQuery}&quot;.</p>
        )}

        {fundsQuery.data != null && fundsQuery.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-border">
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Name</th>
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Family</th>
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Type</th>
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Min invest</th>
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Fractional</th>
                  <th className="pb-2 text-xs font-medium text-muted-foreground">Currency</th>
                </tr>
              </thead>
              <tbody>
                {fundsQuery.data.map((f) => (
                  <FundRow key={f.id} fund={f} />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {debouncedQuery.length === 0 && (
          <p className="text-sm text-muted-foreground">Enter a search term to find mutual funds.</p>
        )}
      </section>
    </main>
  );
}
