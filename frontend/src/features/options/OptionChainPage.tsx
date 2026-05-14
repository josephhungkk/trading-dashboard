import * as React from 'react';
import { useNavigate, useSearch } from '@tanstack/react-router';

import { OptionChainTable } from '@/features/options/OptionChainTable';
import { OptionExpiryTabs } from '@/features/options/OptionExpiryTabs';
import { useOptionChain } from '@/features/options/hooks/useOptionChain';
import { useOptionExpirations } from '@/features/options/hooks/useOptionExpirations';
import { Route } from '@/routes/options.chain';

export function OptionChainPage(): React.JSX.Element {
  const search = useSearch({ from: Route.id });
  const navigate = useNavigate({ from: Route.id });

  const symbol: string = typeof search.symbol === 'string' ? search.symbol : '';
  const expiry: string | null =
    typeof search.expiry === 'string' ? search.expiry : null;

  const updateSymbol = (next: string): void => {
    void navigate({ search: (prev) => ({ ...prev, symbol: next, expiry: undefined }) });
  };

  const updateExpiry = (next: string): void => {
    void navigate({ search: (prev) => ({ ...prev, expiry: next }) });
  };

  const expirations = useOptionExpirations(symbol);
  const chain = useOptionChain(symbol, expiry);

  const resolvedExpiry = expiry ?? expirations.data?.[0];

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="option-chain-page">
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:gap-4">
        <h1 className="text-2xl font-semibold">Option Chain</h1>
        <input
          aria-label="Underlying symbol"
          data-testid="symbol-input"
          placeholder="e.g. AAPL"
          value={symbol}
          onChange={(e) => updateSymbol(e.currentTarget.value.toUpperCase())}
          className="w-full rounded-md border border-border bg-panel p-2 text-sm md:w-auto"
        />
      </div>

      {symbol === '' && (
        <p className="text-sm text-muted-foreground">
          Enter a symbol to load expirations and strikes.
        </p>
      )}

      {symbol !== '' && expirations.isLoading && (
        <p className="text-sm text-muted-foreground" data-testid="expirations-loading">
          Loading expirations…
        </p>
      )}

      {symbol !== '' && expirations.isError && (
        <p className="text-sm text-red-600" data-testid="expirations-error">
          Failed to load expirations: {expirations.error instanceof Error ? expirations.error.message : 'unknown error'}
        </p>
      )}

      {expirations.data && expirations.data.length > 0 && (
        <OptionExpiryTabs
          expirations={expirations.data}
          selected={resolvedExpiry ?? expirations.data[0] ?? null}
          onSelect={updateExpiry}
        />
      )}

      {expirations.data && expirations.data.length === 0 && symbol !== '' && (
        <p className="text-sm text-muted-foreground" data-testid="expirations-empty">
          No expirations found for {symbol}.
        </p>
      )}

      {resolvedExpiry !== undefined && chain.isLoading && (
        <p className="text-sm text-muted-foreground" data-testid="chain-loading">
          Loading chain for {resolvedExpiry}…
        </p>
      )}

      {resolvedExpiry !== undefined && chain.isError && (
        <p className="text-sm text-red-600" data-testid="chain-error">
          Failed to load chain: {chain.error instanceof Error ? chain.error.message : 'unknown error'}
        </p>
      )}

      {chain.data && (
        <OptionChainTable data={chain.data} spot={null} />
      )}
    </div>
  );
}
