import * as React from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { Tabs, TabsList, TabsTrigger } from '@/components/primitives/Tabs';
import { Badge } from '@/components/primitives/Badge';
import { NumericCell } from '@/components/primitives/NumericCell';
import { DataTable } from '@/components/patterns/DataTable';
import { MobileCardRow } from '@/components/patterns/MobileCardRow/MobileCardRow';
import { useActiveStores } from '@/stores/registry';
import type { Order, OrderStatus, OrderSide } from '@/services/types';

type TabKey = 'open' | 'filled' | 'cancelled' | 'all';

const TAB_ORDER: readonly TabKey[] = ['open', 'filled', 'cancelled', 'all'] as const;

/**
 * Orders page — 4 tabs (Open / Filled / Cancelled / All) over a virtualized
 * DataTable with a mobile card fallback. Open tab includes both `open` and
 * `partial` order statuses.
 */
export function OrdersPage(): React.JSX.Element {
  const { useOrders } = useActiveStores();
  const orders = useOrders((s) => s.orders);
  const [tab, setTab] = React.useState<TabKey>('open');

  const filtered = React.useMemo(() => filterOrdersByTab(orders, tab), [orders, tab]);

  return (
    <section className="flex h-full flex-col gap-3 p-4" aria-label="Orders">
      <header className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold text-fg">Orders</h2>
        <p className="text-xs text-fg-muted">{filtered.length} order(s)</p>
      </header>

      <Tabs value={tab} onValueChange={(v) => setTab(v as TabKey)}>
        <TabsList>
          {TAB_ORDER.map((k) => (
            <TabsTrigger key={k} value={k}>
              {labelForTab(k)}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <div
        className="h-[calc(100vh-14rem)] rounded-lg border border-border bg-panel"
        role="tabpanel"
        aria-label={`${labelForTab(tab)} orders`}
      >
        <DataTable<Order>
          columns={ORDER_COLUMNS}
          data={filtered}
          rowKey={(o) => o.id}
          mobileRow={(o) => (
            <MobileCardRow
              primary={o.symbol}
              secondary={`${o.orderType} · ${o.side}`}
              metrics={[
                { label: 'Qty', value: formatOrderQty(o) },
                { label: 'Status', value: o.status },
                { label: 'Time', value: formatTime(o.createdAt) },
              ]}
            />
          )}
        />
      </div>
    </section>
  );
}

function labelForTab(k: TabKey): string {
  if (k === 'open') return 'Open';
  if (k === 'filled') return 'Filled';
  if (k === 'cancelled') return 'Cancelled';
  return 'All';
}

export function filterOrdersByTab(orders: readonly Order[], tab: TabKey): Order[] {
  if (tab === 'all') return [...orders];
  if (tab === 'open') return orders.filter((o) => o.status === 'open' || o.status === 'partial');
  return orders.filter((o) => o.status === tab);
}

function formatOrderQty(o: Order): string {
  if (o.status === 'partial') return `${formatNum(o.filledQty)}/${formatNum(o.qty)}`;
  return formatNum(o.qty);
}

function formatNum(n: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(n);
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return iso;
  }
}

function sideVariant(side: OrderSide): 'up' | 'down' {
  return side === 'buy' ? 'up' : 'down';
}

function statusVariant(
  status: OrderStatus,
): 'up' | 'down' | 'warn' | 'neutral' {
  if (status === 'filled') return 'up';
  if (status === 'cancelled' || status === 'rejected' || status === 'expired') return 'down';
  if (status === 'partial') return 'warn';
  return 'neutral';
}

const ORDER_COLUMNS: ColumnDef<Order>[] = [
  {
    accessorKey: 'symbol',
    header: 'Symbol',
    enableSorting: true,
    cell: (info) => (
      <span className="font-mono text-fg">{info.getValue<string>()}</span>
    ),
  },
  {
    accessorKey: 'side',
    header: 'Side',
    cell: (info) => {
      const v = info.getValue<OrderSide>();
      return <Badge variant={sideVariant(v)}>{v}</Badge>;
    },
  },
  {
    accessorKey: 'qty',
    header: 'Qty',
    cell: (info) => (
      <span className="font-mono tabular-nums text-right inline-block">
        {formatOrderQty(info.row.original)}
      </span>
    ),
  },
  {
    accessorKey: 'orderType',
    header: 'Type',
    cell: (info) => <span className="text-fg-muted">{info.getValue<string>()}</span>,
  },
  {
    id: 'limit',
    header: 'Limit/Stop',
    cell: (info) => {
      const o = info.row.original;
      const v = o.limitPx ?? o.stopPx;
      if (v == null) return <span className="text-fg-muted">—</span>;
      return <NumericCell value={v} format="number" digits={2} />;
    },
  },
  {
    accessorKey: 'status',
    header: 'Status',
    cell: (info) => {
      const v = info.getValue<OrderStatus>();
      return <Badge variant={statusVariant(v)}>{v}</Badge>;
    },
  },
  {
    accessorKey: 'createdAt',
    header: 'Created',
    cell: (info) => {
      const v = info.getValue<string>();
      let text = v;
      try {
        text = new Date(v).toLocaleString();
      } catch {
        /* keep ISO fallback */
      }
      return <span className="text-fg-muted">{text}</span>;
    },
  },
];
