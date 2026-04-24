import * as React from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { Badge } from '@/components/primitives/Badge';
import { DataTable } from '@/components/patterns/DataTable';
import { MobileCardRow } from '@/components/patterns/MobileCardRow/MobileCardRow';
import { useActiveStores } from '@/stores/registry';
import type { Order, OrderSide, OrderStatus } from '@/services/types';

/**
 * Compact open-orders list for RightPanel. Shows only open/partial orders and
 * a reduced column set (symbol, side, qty, status).
 */
export function OpenOrdersCompact(): React.JSX.Element {
  const { useOrders } = useActiveStores();
  const orders = useOrders((s) => s.orders);
  const open = React.useMemo(
    () => orders.filter((o) => o.status === 'open' || o.status === 'partial'),
    [orders],
  );

  return (
    <section className="flex h-full flex-col gap-2 p-2" aria-label="Open Orders">
      <header className="flex items-baseline justify-between px-1">
        <h2 className="text-xs font-semibold text-fg">Open Orders</h2>
        <span className="text-[0.625rem] text-fg-muted">{open.length}</span>
      </header>
      <div className="h-full min-h-0 rounded-md border border-border bg-panel">
        <DataTable<Order>
          columns={COMPACT_COLUMNS}
          data={open}
          rowKey={(o) => o.id}
          rowHeight={28}
          mobileRow={(o) => (
            <MobileCardRow
              primary={o.symbol}
              secondary={o.side}
              metrics={[
                { label: 'Qty', value: formatQty(o) },
                { label: 'Status', value: o.status },
              ]}
            />
          )}
        />
      </div>
    </section>
  );
}

function formatQty(o: Order): string {
  if (o.status === 'partial') return `${o.filledQty}/${o.qty}`;
  return String(o.qty);
}

function sideVariant(side: OrderSide): 'up' | 'down' {
  return side === 'buy' ? 'up' : 'down';
}

function statusVariant(s: OrderStatus): 'up' | 'down' | 'warn' | 'neutral' {
  if (s === 'partial') return 'warn';
  return 'neutral';
}

const COMPACT_COLUMNS: ColumnDef<Order>[] = [
  {
    accessorKey: 'symbol',
    header: 'Symbol',
    cell: (info) => (
      <span className="font-mono text-xs text-fg">{info.getValue<string>()}</span>
    ),
  },
  {
    accessorKey: 'side',
    header: 'Side',
    cell: (info) => {
      const v = info.getValue<OrderSide>();
      return (
        <Badge variant={sideVariant(v)} className="text-[0.625rem]">
          {v}
        </Badge>
      );
    },
  },
  {
    accessorKey: 'qty',
    header: 'Qty',
    cell: (info) => (
      <span className="font-mono tabular-nums text-xs text-fg">
        {formatQty(info.row.original)}
      </span>
    ),
  },
  {
    accessorKey: 'status',
    header: 'Status',
    cell: (info) => {
      const v = info.getValue<OrderStatus>();
      return (
        <Badge variant={statusVariant(v)} className="text-[0.625rem]">
          {v}
        </Badge>
      );
    },
  },
];
