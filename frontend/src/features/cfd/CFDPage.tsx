import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { searchCFDs } from '@/services/cfd/api';
import type { CFDInstrument } from '@/services/cfd/types';

function CFDRow({ cfd }: { readonly cfd: CFDInstrument }): React.JSX.Element {
  const { meta } = cfd;
  return (
    <tr className="border-b border-border last:border-0">
      <td className="py-3 pr-4 text-sm font-medium">{cfd.display_name ?? cfd.canonical_id}</td>
      <td className="py-3 pr-4 text-sm text-muted-foreground">{meta.underlying_type ?? '—'}</td>
      <td className="py-3 pr-4 text-sm font-mono">{meta.underlying_symbol ?? '—'}</td>
      <td className="py-3 pr-4 text-sm font-mono">
        {meta.max_leverage != null ? `${meta.max_leverage}×` : '—'}
      </td>
      <td className="py-3 pr-4 text-sm font-mono">
        {meta.margin_rate != null ? `${meta.margin_rate}%` : '—'}
      </td>
      <td className="py-3 text-sm">{cfd.currency}</td>
    </tr>
  );
}

export function CFDPage(): React.JSX.Element {
  const [query, setQuery] = React.useState('');
  const [debouncedQuery, setDebouncedQuery] = React.useState('');

  React.useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQuery(query), 300);
    return () => window.clearTimeout(t);
  }, [query]);

  const cfdsQuery = useQuery({
    queryKey: ['cfd', 'search', debouncedQuery],
    queryFn: () => (debouncedQuery.length >= 1 ? searchCFDs(debouncedQuery) : Promise.resolve([])),
    staleTime: 30_000,
  });

  return (
    <main className="max-w-5xl mx-auto p-4 space-y-6" data-testid="cfd-page">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">CFDs</h1>
      </div>

      <section className="space-y-3">
        <input
          type="search"
          placeholder="Search by name or underlying symbol…"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
          aria-label="Search CFDs"
        />

        {cfdsQuery.isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {cfdsQuery.isError && (
          <p className="text-sm text-red-600">Failed to load CFDs.</p>
        )}

        {cfdsQuery.data != null && cfdsQuery.data.length === 0 && debouncedQuery.length >= 1 && (
          <p className="text-sm text-muted-foreground">No CFDs found for &quot;{debouncedQuery}&quot;.</p>
        )}

        {cfdsQuery.data != null && cfdsQuery.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-border">
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Name</th>
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Type</th>
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Underlying</th>
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Max leverage</th>
                  <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Margin</th>
                  <th className="pb-2 text-xs font-medium text-muted-foreground">Currency</th>
                </tr>
              </thead>
              <tbody>
                {cfdsQuery.data.map((c) => (
                  <CFDRow key={c.id} cfd={c} />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {debouncedQuery.length === 0 && (
          <p className="text-sm text-muted-foreground">Enter a search term to find CFDs.</p>
        )}
      </section>
    </main>
  );
}
