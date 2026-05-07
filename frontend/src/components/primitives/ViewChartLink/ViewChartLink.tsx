/**
 * ViewChartLink — reusable "View Chart" navigation link for rows (MED-F).
 *
 * Renders a TanStack Router Link to /chart/$canonicalId with a LineChart icon.
 * Returns null when canonicalId is null or undefined (graceful no-op).
 */
import * as React from 'react';
import { Link } from '@tanstack/react-router';
import { LineChart } from 'lucide-react';

export interface ViewChartLinkProps {
  /** Phase-9 canonical symbol id, e.g. "AAPL.US". Absent until data wiring. */
  canonicalId: string | null | undefined;
}

export function ViewChartLink({ canonicalId }: ViewChartLinkProps): React.JSX.Element | null {
  if (!canonicalId) return null;
  return (
    <Link
      to="/chart/$canonicalId"
      params={{ canonicalId }}
      aria-label="View Chart"
      className="inline-flex items-center gap-1 hover:underline"
    >
      <LineChart className="h-4 w-4" aria-hidden="true" />
      <span className="hidden md:inline">View Chart</span>
    </Link>
  );
}
