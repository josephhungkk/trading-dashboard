import * as React from 'react';
import { Button } from '@/components/primitives/Button';
import { cn } from '@/lib/utils';

/**
 * Structural shape matching components['schemas']['FillResponse'] from api-generated.
 * Defined inline to keep the patterns layer free of services/ imports.
 */
export interface FillResponse {
  /** UUID */
  id: string;
  /** UUID */
  order_id: string;
  exec_id: string;
  executed_at: string;
  qty: string;
  price: string;
  currency: string;
  commission?: string | null;
  commission_currency?: string | null;
}

export interface FillsTableProps {
  fills: FillResponse[];
  hasMore?: boolean;
  onLoadMore?: () => void;
  isLoading?: boolean;
}

function formatCurrency(value: string, currency: string): string {
  const num = parseFloat(value);
  if (Number.isNaN(num)) return value;
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 8,
    }).format(num);
  } catch {
    return `${currency} ${num.toFixed(2)}`;
  }
}

function toCalendarDay(isoString: string): string {
  // Returns YYYY-MM-DD in UTC
  return isoString.slice(0, 10);
}

function formatGroupLabel(day: string): string {
  // day is YYYY-MM-DD; format as "Mon, 28 Apr 2026"
  const d = new Date(day + 'T00:00:00Z');
  return d.toUTCString().slice(0, 16);
}

function formatTime(isoString: string): string {
  const d = new Date(isoString);
  return d.toISOString().slice(11, 19); // HH:MM:SS
}

interface DayGroup {
  day: string; // YYYY-MM-DD
  fills: FillResponse[];
  dayTotal: number;
  dayCurrency: string;
}

function groupByDay(fills: FillResponse[]): DayGroup[] {
  const map = new Map<string, FillResponse[]>();
  for (const fill of fills) {
    const day = toCalendarDay(fill.executed_at);
    const bucket = map.get(day);
    if (bucket) {
      bucket.push(fill);
    } else {
      map.set(day, [fill]);
    }
  }
  return Array.from(map.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, dayFills]) => {
      const total = dayFills.reduce((sum, f) => {
        return sum + parseFloat(f.qty) * parseFloat(f.price);
      }, 0);
      const currency = dayFills[0]?.currency ?? 'USD';
      return { day, fills: dayFills, dayTotal: total, dayCurrency: currency };
    });
}

function truncateOrderId(orderId: string): string {
  return orderId.slice(0, 8) + '…';
}

const COL_COUNT = 7;

export function FillsTable({
  fills,
  hasMore = false,
  onLoadMore,
  isLoading = false,
}: FillsTableProps): React.JSX.Element {
  const groups = React.useMemo(() => groupByDay(fills), [fills]);

  return (
    <div className="flex flex-col h-full overflow-auto">
      <table className="w-full text-sm border-collapse" aria-label="Fills">
        <thead className="sticky top-0 bg-panel z-10">
          <tr>
            <th scope="col" className="px-3 py-2 text-left text-fg-muted font-medium whitespace-nowrap">
              Time (UTC)
            </th>
            <th scope="col" className="px-3 py-2 text-left text-fg-muted font-medium whitespace-nowrap">
              Symbol
            </th>
            <th scope="col" className="px-3 py-2 text-left text-fg-muted font-medium">
              Side
            </th>
            <th scope="col" className="px-3 py-2 text-right text-fg-muted font-medium">
              Qty
            </th>
            <th scope="col" className="px-3 py-2 text-right text-fg-muted font-medium">
              Price
            </th>
            <th scope="col" className="px-3 py-2 text-right text-fg-muted font-medium">
              Commission
            </th>
            <th scope="col" className="px-3 py-2 text-right text-fg-muted font-medium">
              Total
            </th>
          </tr>
        </thead>
        <tbody>
          {fills.length === 0 && !isLoading && (
            <tr>
              <td
                colSpan={COL_COUNT}
                className="py-12 text-center text-fg-muted"
              >
                No fills in this date range
              </td>
            </tr>
          )}
          {fills.length === 0 && isLoading && (
            <tr>
              <td colSpan={COL_COUNT} className="py-12 text-center text-fg-muted">
                <span className="animate-pulse">Loading…</span>
              </td>
            </tr>
          )}
          {groups.map((group) => (
            <React.Fragment key={group.day}>
              <tr className="bg-muted/5">
                <th
                  scope="rowgroup"
                  colSpan={COL_COUNT - 1}
                  className="px-3 py-1 text-left text-xs font-semibold text-fg-muted"
                >
                  {formatGroupLabel(group.day)}
                </th>
                <td className="px-3 py-1 text-right text-xs font-semibold text-fg-muted tabular-nums">
                  {formatCurrency(group.dayTotal.toFixed(8), group.dayCurrency)}
                </td>
              </tr>
              {group.fills.map((fill) => {
                const total = parseFloat(fill.qty) * parseFloat(fill.price);
                return (
                  <tr
                    key={fill.id}
                    className={cn(
                      'border-b border-border/40 hover:bg-muted/5 transition-colors',
                    )}
                  >
                    <td className="px-3 py-2 tabular-nums font-mono text-xs text-fg-muted">
                      {formatTime(fill.executed_at)}
                    </td>
                    <td className="px-3 py-2 font-medium text-fg">
                      {truncateOrderId(fill.order_id)}
                    </td>
                    <td className="px-3 py-2 text-fg">—</td>
                    <td className="px-3 py-2 text-right tabular-nums font-mono">
                      {parseFloat(fill.qty).toFixed(4)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums font-mono">
                      {formatCurrency(fill.price, fill.currency)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums font-mono text-fg-muted">
                      {fill.commission != null && fill.commission_currency != null
                        ? formatCurrency(fill.commission, fill.commission_currency)
                        : '—'}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums font-mono font-semibold">
                      {formatCurrency(total.toFixed(8), fill.currency)}
                    </td>
                  </tr>
                );
              })}
            </React.Fragment>
          ))}
        </tbody>
      </table>
      {hasMore && (
        <div className="flex justify-center py-4">
          <Button
            variant="outline"
            size="sm"
            onClick={onLoadMore}
            disabled={isLoading}
          >
            {isLoading ? 'Loading…' : 'Load more'}
          </Button>
        </div>
      )}
    </div>
  );
}
