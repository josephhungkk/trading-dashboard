import * as React from 'react';
import { useQuery } from '@tanstack/react-query';

interface ChartPageProps {
  canonicalId: string;
}

export function ChartPage({ canonicalId }: ChartPageProps): React.JSX.Element {
  const { isLoading, error } = useQuery({
    queryKey: ['chart-layouts', canonicalId],
    queryFn: async () => {
      // STUB: instrument_id resolution deferred to Task 36.
      // For now, return null — full impl in Task 36.
      return null;
    },
  });

  return (
    <div className="flex h-full flex-col p-2">
      <h1 className="text-lg font-semibold">Chart — {canonicalId}</h1>
      <div data-testid="trade-chart" className="flex-1 rounded border border-border">
        {isLoading && <p>Loading…</p>}
        {error && <p role="alert">Failed to load chart</p>}
        {!isLoading && !error && (
          <p className="text-muted-foreground">TradeChart placeholder (Task 36)</p>
        )}
      </div>
    </div>
  );
}
