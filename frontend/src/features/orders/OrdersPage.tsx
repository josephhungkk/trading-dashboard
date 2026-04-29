import * as React from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from '@/components/primitives/Dialog';
import { Badge } from '@/components/primitives/Badge';
import { Button } from '@/components/primitives/Button';
import { DataTable } from '@/components/patterns/DataTable';
import { MobileCardRow } from '@/components/patterns/MobileCardRow/MobileCardRow';
import { TradeTicketModal } from '@/components/patterns/TradeTicketModal/TradeTicketModal';
import { useToast } from '@/hooks/use-toast';
import { useOrdersList } from '@/hooks/useOrdersList';
import { useOrdersStream } from '@/hooks/useOrdersStream';
import { cancelOrder } from '@/services/orders';
import {
  useOrdersStore,
  type BrokerMaintenance as StoreBrokerMaintenance,
  type OrderResponse as StoreOrderResponse,
} from '@/stores/global/orders';

type UiOrderStatus =
  | 'pending_submit'
  | 'pending'
  | 'submitted'
  | 'partial'
  | 'filled'
  | 'cancelled'
  | 'rejected'
  | 'expired'
  | 'inactive'
  | 'open';

interface UiOrder {
  id: string;
  accountId: string;
  conid: string;
  symbol: string;
  side: 'BUY' | 'SELL' | 'buy' | 'sell';
  qty: string;
  orderType: string;
  limitPrice: string | null;
  status: UiOrderStatus;
  filledQty: string;
  avgFillPrice: string | null;
  createdAt: string;
  lastEventAt: string | null;
}

interface OrdersPageStorySnapshot {
  orders: StoreOrderResponse[];
  killSwitchActive?: boolean;
  brokerMaintenance?: StoreBrokerMaintenance | null;
}

interface OrdersPageProps {
  storySnapshot?: OrdersPageStorySnapshot;
}

const ACTIVE_STATUSES = new Set<UiOrderStatus>(['pending_submit', 'pending', 'submitted', 'partial', 'open']);
const TERMINAL_STATUSES = new Set<UiOrderStatus>(['filled', 'cancelled', 'rejected', 'expired']);
const HISTORY_PAGE_SIZE = 5;

export function OrdersPage({ storySnapshot }: OrdersPageProps = {}): React.JSX.Element {
  const { fetchAndSync, isLoading, error } = useOrdersList();
  useOrdersStream();

  const { toast } = useToast();
  const ordersById = useOrdersStore((s) => s.orders);
  const killSwitchActive = useOrdersStore((s) => s.killSwitchActive);
  const brokerMaintenance = useOrdersStore((s) => s.brokerMaintenance);
  const [historyPage, setHistoryPage] = React.useState(0);
  const [orderToCancel, setOrderToCancel] = React.useState<UiOrder | null>(null);
  const [cancelInFlight, setCancelInFlight] = React.useState(false);
  const [modifyTarget, setModifyTarget] = React.useState<UiOrder | null>(null);

  React.useEffect(() => {
    if (storySnapshot) {
      const store = useOrdersStore.getState();
      store.clear();
      for (const order of storySnapshot.orders) store.addOrder(order);
      store.setKillSwitchActive(storySnapshot.killSwitchActive ?? false);
      store.setBrokerMaintenance(storySnapshot.brokerMaintenance ?? null);
      return;
    }
    void fetchAndSync();
  }, [fetchAndSync, storySnapshot]);

  const orders = React.useMemo(
    () => Object.values(ordersById).map(normalizeOrder).sort(compareOrdersDesc),
    [ordersById],
  );
  const activeOrders = React.useMemo(
    () => orders.filter((order) => ACTIVE_STATUSES.has(order.status)),
    [orders],
  );
  const historyOrders = React.useMemo(
    () => orders.filter((order) => TERMINAL_STATUSES.has(order.status)),
    [orders],
  );
  const pageCount = Math.max(1, Math.ceil(historyOrders.length / HISTORY_PAGE_SIZE));
  const clampedHistoryPage = Math.min(historyPage, pageCount - 1);
  const visibleHistory = historyOrders.slice(
    clampedHistoryPage * HISTORY_PAGE_SIZE,
    clampedHistoryPage * HISTORY_PAGE_SIZE + HISTORY_PAGE_SIZE,
  );

  const columns = React.useMemo(
    () => createOrderColumns((order) => setOrderToCancel(order), (order) => setModifyTarget(order)),
    [],
  );

  async function handleConfirmCancel(): Promise<void> {
    if (!orderToCancel) return;

    setCancelInFlight(true);
    try {
      await cancelOrder(orderToCancel.id);
      toast({ title: 'Cancel requested', description: `Order #${orderToCancel.id}`, tone: 'success' });
      setOrderToCancel(null);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught);
      toast({ title: 'Cancel failed', description: message, tone: 'error' });
    } finally {
      setCancelInFlight(false);
    }
  }

  return (
    <section className="flex h-full flex-col gap-4 p-4" aria-label="Orders">
      <header className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold text-fg">Orders</h2>
        <p className="text-xs text-fg-muted">
          {activeOrders.length} active / {historyOrders.length} history
        </p>
      </header>

      <BannerStack
        killSwitchActive={killSwitchActive}
        maintenanceActive={brokerMaintenance?.active === true}
        maintenanceWindow={brokerMaintenance?.window ?? null}
        maintenanceUntil={brokerMaintenance?.until ?? null}
      />

      {error ? (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-fg" role="alert">
          {error.message}
        </p>
      ) : null}

      <OrderSection
        title="Active orders"
        description={isLoading ? 'Loading orders' : `${activeOrders.length} order(s)`}
        tableLabel="Active orders table"
        emptyText="No active orders"
        columns={columns}
        orders={activeOrders}
      />

      <section className="flex min-h-0 flex-1 flex-col gap-2" aria-label="Recent history">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-fg">Recent history</h3>
            <p className="text-xs text-fg-muted">{historyOrders.length} terminal order(s)</p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setHistoryPage((page) => Math.max(0, page - 1))}
              disabled={clampedHistoryPage === 0}
            >
              Previous
            </Button>
            <span className="min-w-12 text-center text-xs text-fg-muted">
              {clampedHistoryPage + 1} / {pageCount}
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setHistoryPage((page) => Math.min(Math.max(0, pageCount - 1), page + 1))}
              disabled={clampedHistoryPage >= pageCount - 1}
            >
              Next
            </Button>
          </div>
        </div>

        <OrderTable
          ariaLabel="Recent history table"
          emptyText="No recent history"
          columns={columns}
          orders={visibleHistory}
        />
      </section>

      {modifyTarget !== null ? (
        <TradeTicketModal
          mode="modify"
          accountId={modifyTarget.accountId}
          orderId={modifyTarget.id}
          symbol={modifyTarget.symbol}
          initialOrder={{
            conid: modifyTarget.conid,
            side: modifyTarget.side === 'buy' ? 'BUY' : modifyTarget.side === 'sell' ? 'SELL' : modifyTarget.side,
            order_type: modifyTarget.orderType === 'LIMIT' || modifyTarget.orderType === 'STOP' ? modifyTarget.orderType : 'MARKET',
            qty: Number(modifyTarget.qty),
            ...(modifyTarget.limitPrice !== null ? { limit_price: Number(modifyTarget.limitPrice) } : {}),
          }}
          onClose={() => { setModifyTarget(null); }}
        />
      ) : null}

      <CancelDialog
        order={orderToCancel}
        inFlight={cancelInFlight}
        onOpenChange={(open) => {
          if (!open && !cancelInFlight) setOrderToCancel(null);
        }}
        onConfirm={() => { void handleConfirmCancel(); }}
      />
    </section>
  );
}

function BannerStack({
  killSwitchActive,
  maintenanceActive,
  maintenanceWindow,
  maintenanceUntil,
}: {
  killSwitchActive: boolean;
  maintenanceActive: boolean;
  maintenanceWindow: string | null;
  maintenanceUntil: string | null;
}): React.JSX.Element | null {
  if (!killSwitchActive && !maintenanceActive) return null;

  return (
    <div className="sticky top-0 z-20 flex flex-col gap-2">
      {killSwitchActive ? (
        <div className="rounded-md border border-destructive/60 bg-destructive/20 p-3 text-sm font-semibold text-fg" role="alert">
          Trading paused by operator
        </div>
      ) : null}
      {maintenanceActive ? (
        <div className="rounded-md border border-warning/60 bg-warning/20 p-3 text-sm font-semibold text-fg" role="status">
          Broker maintenance active{maintenanceWindow ? `: ${maintenanceWindow}` : ''}
          {maintenanceUntil ? ` until ${formatDateTime(maintenanceUntil)}` : ''}
        </div>
      ) : null}
    </div>
  );
}

function OrderSection({
  title,
  description,
  tableLabel,
  emptyText,
  columns,
  orders,
}: {
  title: string;
  description: string;
  tableLabel: string;
  emptyText: string;
  columns: ColumnDef<UiOrder>[];
  orders: UiOrder[];
}): React.JSX.Element {
  return (
    <section className="flex min-h-0 flex-1 flex-col gap-2" aria-label={title}>
      <div>
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
        <p className="text-xs text-fg-muted">{description}</p>
      </div>
      <OrderTable ariaLabel={tableLabel} emptyText={emptyText} columns={columns} orders={orders} />
    </section>
  );
}

function OrderTable({
  ariaLabel,
  emptyText,
  columns,
  orders,
}: {
  ariaLabel: string;
  emptyText: string;
  columns: ColumnDef<UiOrder>[];
  orders: UiOrder[];
}): React.JSX.Element {
  return (
    <div className="min-h-0 flex-1 rounded-lg border border-border bg-panel" aria-label={ariaLabel}>
      {orders.length === 0 ? (
        <div className="flex h-full min-h-32 items-center justify-center text-sm text-fg-muted">
          {emptyText}
        </div>
      ) : (
        <DataTable<UiOrder>
          columns={columns}
          data={orders}
          rowKey={(order) => order.id}
          mobileRow={(order) => (
            <MobileCardRow
              primary={order.symbol}
              secondary={`${order.orderType} · ${order.side}`}
              metrics={[
                { label: 'Qty', value: order.qty },
                { label: 'Filled', value: order.filledQty },
                { label: 'Status', value: order.status },
              ]}
            />
          )}
        />
      )}
    </div>
  );
}

function CancelDialog({
  order,
  inFlight,
  onOpenChange,
  onConfirm,
}: {
  order: UiOrder | null;
  inFlight: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}): React.JSX.Element {
  return (
    <Dialog open={order !== null} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Cancel order #{order?.id}?</DialogTitle>
        <DialogDescription>
          This sends a cancel request to the broker. The row will update when the order stream confirms the change.
        </DialogDescription>
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="outline" disabled={inFlight}>Keep order</Button>
          </DialogClose>
          <Button type="button" variant="destructive" onClick={onConfirm} disabled={inFlight}>
            {inFlight ? 'Cancelling' : 'Confirm cancel'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function createOrderColumns(onCancel: (order: UiOrder) => void, onModify: (order: UiOrder) => void): ColumnDef<UiOrder>[] {
  return [
    {
      accessorKey: 'id',
      header: 'ID',
      cell: (info) => <span className="font-mono text-xs text-fg">{info.getValue<string>()}</span>,
    },
    {
      accessorKey: 'symbol',
      header: 'symbol',
      cell: (info) => <span className="font-mono text-fg">{info.getValue<string>()}</span>,
    },
    {
      accessorKey: 'side',
      header: 'side',
      cell: (info) => {
        const side = info.getValue<UiOrder['side']>();
        return <Badge variant={sideVariant(side)}>{side}</Badge>;
      },
    },
    {
      accessorKey: 'qty',
      header: 'qty',
      cell: (info) => <span className="font-mono tabular-nums">{info.getValue<string>()}</span>,
    },
    {
      accessorKey: 'orderType',
      header: 'type',
      cell: (info) => <span className="text-fg-muted">{info.getValue<string>()}</span>,
    },
    {
      accessorKey: 'status',
      header: 'status',
      cell: (info) => {
        const status = info.getValue<UiOrderStatus>();
        return <Badge variant={statusVariant(status)}>{status}</Badge>;
      },
    },
    {
      accessorKey: 'filledQty',
      header: 'filled_qty',
      cell: (info) => <span className="font-mono tabular-nums">{info.getValue<string>()}</span>,
    },
    {
      accessorKey: 'avgFillPrice',
      header: 'avg_fill_price',
      cell: (info) => {
        const value = info.getValue<string | null>();
        return <span className="font-mono tabular-nums text-fg-muted">{value ?? '-'}</span>;
      },
    },
    {
      id: 'actions',
      header: 'Actions',
      cell: (info) => {
        const order = info.row.original;
        const terminal = TERMINAL_STATUSES.has(order.status);
        return (
          <div className="flex items-center gap-1.5">
            {!terminal ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => { onModify(order); }}
                aria-label={`Modify order ${order.id}`}
              >
                Modify
              </Button>
            ) : null}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => { onCancel(order); }}
              disabled={terminal}
              aria-label={`Cancel order ${order.id}`}
            >
              Cancel
            </Button>
          </div>
        );
      },
    },
  ];
}

function normalizeOrder(order: StoreOrderResponse): UiOrder {
  return {
    id: String(order.id),
    accountId: readString(order, 'account_id', readString(order, 'accountId', '')),
    conid: readString(order, 'conid', ''),
    symbol: readString(order, 'symbol', '-'),
    side: normalizeSide(readString(order, 'side', 'BUY')),
    qty: readString(order, 'qty', '0'),
    orderType: readString(order, 'order_type', readString(order, 'orderType', 'MARKET')),
    limitPrice: readNullableString(order, 'limit_price') ?? readNullableString(order, 'limitPrice'),
    status: normalizeStatus(readString(order, 'status', 'submitted')),
    filledQty: readString(order, 'filled_qty', readString(order, 'filledQty', '0')),
    avgFillPrice: readNullableString(order, 'avg_fill_price') ?? readNullableString(order, 'avgFillPrice'),
    createdAt: readString(order, 'created_at', readString(order, 'createdAt', '')),
    lastEventAt: readNullableString(order, 'last_event_at') ?? readNullableString(order, 'updated_at'),
  };
}

function readString(order: StoreOrderResponse, key: string, fallback: string): string {
  const value = order[key];
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  return fallback;
}

function readNullableString(order: StoreOrderResponse, key: string): string | null {
  const value = order[key];
  if (value === null || value === undefined) return null;
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  return null;
}

function normalizeSide(value: string): UiOrder['side'] {
  if (value === 'SELL' || value === 'sell') return value;
  return value === 'buy' ? 'buy' : 'BUY';
}

function normalizeStatus(value: string): UiOrderStatus {
  if (
    value === 'pending_submit' ||
    value === 'pending' ||
    value === 'submitted' ||
    value === 'partial' ||
    value === 'filled' ||
    value === 'cancelled' ||
    value === 'rejected' ||
    value === 'expired' ||
    value === 'inactive' ||
    value === 'open'
  ) {
    return value;
  }
  return 'submitted';
}

function compareOrdersDesc(a: UiOrder, b: UiOrder): number {
  return orderTime(b) - orderTime(a);
}

function orderTime(order: UiOrder): number {
  const value = order.lastEventAt ?? order.createdAt;
  const time = Date.parse(value);
  return Number.isNaN(time) ? 0 : time;
}

function sideVariant(side: UiOrder['side']): 'up' | 'down' {
  return side.toLowerCase() === 'buy' ? 'up' : 'down';
}

function statusVariant(status: UiOrderStatus): 'up' | 'down' | 'warn' | 'neutral' {
  if (status === 'filled') return 'up';
  if (status === 'cancelled' || status === 'rejected' || status === 'expired') return 'down';
  if (status === 'partial' || status === 'pending' || status === 'pending_submit') return 'warn';
  return 'neutral';
}

function formatDateTime(iso: string): string {
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return iso;
  return new Date(time).toLocaleString();
}
