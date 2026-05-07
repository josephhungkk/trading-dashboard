import * as React from 'react';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { TradeChart } from './TradeChart';
import { ChartToolbar } from './ChartToolbar';
import { TimeframeBar } from './TimeframeBar';
import { DrawingTools } from './DrawingTools';
// getChartLayout import deferred until instrument_id resolution lands (Task 37).

interface ChartPageProps {
  canonicalId: string;
}

export function ChartPage({ canonicalId }: ChartPageProps): React.JSX.Element {
  // MED-C: drawingsOpen lifted from ChartToolbar so ChartPage can show DrawingTools panel.
  // TODO(Chunk G): replace placeholder with full DrawingTools panel integration.
  const [drawingsOpen, setDrawingsOpen] = useState(false);

  // TODO(Task 37): resolve instrument_id from canonicalId via API.
  // For now pass 0 as a placeholder; getChartLayout returns null for unknown ids.
  const { isLoading, error } = useQuery({
    queryKey: ['chart-layouts', canonicalId],
    // MED-E: disabled until instrument_id resolution lands; avoids spurious 404s.
    // TODO(Task 37): enable when instrument_id resolution is wired.
    queryFn: async () => null,
    enabled: false,
  });

  return (
    <div className="flex h-full flex-col" data-chart-container>
      <ChartToolbar
        drawingsOpen={drawingsOpen}
        onToggleDrawings={() => setDrawingsOpen((prev) => !prev)}
      />
      <h1 className="px-2 pt-1 text-lg font-semibold">Chart — {canonicalId}</h1>
      <div className="relative flex min-h-0 flex-1">
        {/* Drawings panel — Chunk G integration pending */}
        {drawingsOpen && (
          <div data-testid="drawings-panel" className="w-12 shrink-0">
            <DrawingTools />
          </div>
        )}
        <div className="relative min-h-0 flex-1 rounded border border-border">
          {isLoading && <p>Loading…</p>}
          {error && <p role="alert">Failed to load chart</p>}
          {!isLoading && !error && <TradeChart canonicalId={canonicalId} />}
        </div>
      </div>
      <TimeframeBar />
    </div>
  );
}
