import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { TradeChart } from './TradeChart';
import { getChartLayout } from './services/chartLayouts';

interface ChartPageProps {
  canonicalId: string;
}

export function ChartPage({ canonicalId }: ChartPageProps): React.JSX.Element {
  // TODO(Task 37): resolve instrument_id from canonicalId via API.
  // For now pass 0 as a placeholder; getChartLayout returns null for unknown ids.
  const { isLoading, error } = useQuery({
    queryKey: ['chart-layouts', canonicalId],
    queryFn: () => getChartLayout(0),
  });

  return (
    <div className="flex h-full flex-col p-2">
      <h1 className="text-lg font-semibold">Chart — {canonicalId}</h1>
      <div className="relative flex-1 rounded border border-border">
        {isLoading && <p>Loading…</p>}
        {error && <p role="alert">Failed to load chart</p>}
        {!isLoading && !error && <TradeChart canonicalId={canonicalId} />}
      </div>
    </div>
  );
}
