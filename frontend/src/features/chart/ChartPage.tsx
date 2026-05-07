import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { TradeChart } from './TradeChart';
import { ChartToolbar } from './ChartToolbar';
import { TimeframeBar } from './TimeframeBar';
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
    <div className="flex h-full flex-col" data-chart-container>
      <ChartToolbar />
      <h1 className="px-2 pt-1 text-lg font-semibold">Chart — {canonicalId}</h1>
      <div className="relative min-h-0 flex-1 rounded border border-border">
        {isLoading && <p>Loading…</p>}
        {error && <p role="alert">Failed to load chart</p>}
        {!isLoading && !error && <TradeChart canonicalId={canonicalId} />}
      </div>
      <TimeframeBar />
    </div>
  );
}
