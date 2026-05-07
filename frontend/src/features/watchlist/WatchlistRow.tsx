/**
 * WatchlistRow — renders a single watchlist symbol with an inline "View Chart" link.
 *
 * canonical_id is not yet wired into the watchlist symbol data (Phase-9 data
 * migration pending). The link is omitted gracefully when the field is absent.
 * TODO(task39): wire canonical_id into WatchlistRowData once Phase-9
 * canonicalisation is complete and remove the null-guard here.
 */
import * as React from 'react';
import { Link } from '@tanstack/react-router';
import { LineChart } from 'lucide-react';

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
        {canonicalId ? (
          <Link
            to="/chart/$canonicalId"
            params={{ canonicalId }}
            aria-label="View Chart"
            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-fg-muted hover:bg-muted/20 hover:text-fg"
          >
            <LineChart className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="hidden md:inline">View Chart</span>
          </Link>
        ) : null}
      </div>
    </div>
  );
}
