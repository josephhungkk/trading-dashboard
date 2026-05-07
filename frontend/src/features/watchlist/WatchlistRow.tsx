/**
 * WatchlistRow — renders a single watchlist symbol with an inline "View Chart" link.
 *
 * canonical_id is not yet wired into the watchlist symbol data (Phase-9 data
 * migration pending). The link is omitted gracefully when the field is absent.
 * TODO(task39): wire canonical_id into WatchlistRowData once Phase-9
 * canonicalisation is complete and remove the null-guard here.
 */
import * as React from 'react';
import { ViewChartLink } from '@/components/primitives/ViewChartLink';

export interface WatchlistRowData {
  symbol: string;
  /** Phase-9 canonical symbol id, e.g. "AAPL.US". Absent until data wiring. */
  canonical_id?: string | null;
}

interface WatchlistRowProps {
  row: WatchlistRowData;
}

export function WatchlistRow({ row }: WatchlistRowProps): React.JSX.Element {
  const canonicalId = row.canonical_id ?? null;

  return (
    <div className="flex items-center justify-between gap-2 border-b border-border px-2 py-1.5 text-xs last:border-b-0">
      <span className="font-mono text-fg">{row.symbol}</span>
      <div className="flex items-center gap-2">
        <ViewChartLink canonicalId={canonicalId} />
      </div>
    </div>
  );
}
