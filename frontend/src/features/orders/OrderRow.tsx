/**
 * OrderRow — renders a single order with an inline "View Chart" link.
 *
 * canonical_id is not yet wired into the order data shape (Phase-9 data
 * migration pending). The link is omitted gracefully when the field is absent.
 * TODO(task39): wire canonical_id into order data once Phase-9 canonicalisation
 * is complete and remove the null-guard here.
 */
import * as React from 'react';
import { ViewChartLink } from '@/components/primitives/ViewChartLink';

export interface OrderRowData {
  id: string;
  symbol: string;
  side: string;
  qty: string;
  status: string;
  /** Phase-9 canonical symbol id, e.g. "AAPL.US". Absent until data wiring. */
  canonical_id?: string | null;
}

interface OrderRowProps {
  order: OrderRowData;
}

export function OrderRow({ order }: OrderRowProps): React.JSX.Element {
  const canonicalId = order.canonical_id ?? null;

  return (
    <div className="flex items-center justify-between gap-2 border-b border-border px-2 py-1.5 text-xs last:border-b-0">
      <span className="font-mono text-fg">{order.symbol}</span>
      <div className="flex items-center gap-2">
        <ViewChartLink canonicalId={canonicalId} />
      </div>
    </div>
  );
}
