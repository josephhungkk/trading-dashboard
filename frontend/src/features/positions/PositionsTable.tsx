import * as React from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { NumericCell } from '@/components/primitives/NumericCell';
import { DataTable } from '@/components/patterns/DataTable';
import { MobileCardRow } from '@/components/patterns/MobileCardRow/MobileCardRow';
import { TradeButton } from '@/features/orders/TradeButton';
import { TradeTicketModal } from '@/features/orders/TradeTicketModal';
import { useActiveStores } from '@/stores/registry';
import { useFleetMaintenance } from '@/stores/global/fleet-maintenance';
import type { Account, Position } from '@/services/types';

interface Group {
  accountId: string;
  alias: string;
  broker: string;
  baseCurrency: string;
  positions: Position[];
}

interface PolicyResponse {
  trade_enabled: boolean;
}

interface PositionWithConid extends Position {
  conid?: string | number;
}

export function PositionsTable(): React.JSX.Element {
  const { useAccounts, usePositions } = useActiveStores();
  const accounts = useAccounts((s) => s.accounts);
  const positions = usePositions((s) => s.positions);
  const maintenance = useFleetMaintenance((s) => s.maintenance);

  const groups = React.useMemo(() => groupByAccount(positions, accounts), [positions, accounts]);
  const accountIds = React.useMemo(() => groups.map((group) => group.accountId), [groups]);
  const [tradePolicies, setTradePolicies] = React.useState<Record<string, boolean>>({});

  React.useEffect(() => {
    const controller = new AbortController();
    void Promise.all(
      accountIds.map(async (accountId) => [accountId, await fetchTradeEnabled(accountId, controller.signal)] as const),
    )
      .then((entries) => {
        if (controller.signal.aborted) return;
        setTradePolicies(Object.fromEntries(entries));
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setTradePolicies(Object.fromEntries(accountIds.map((accountId) => [accountId, false])));
      });
    return () => controller.abort();
  }, [accountIds]);

  const columns = React.useMemo(
    () => createPositionColumns(tradePolicies, maintenance.active),
    [tradePolicies, maintenance.active],
  );

  return (
    <>
      {groups.length === 0 ? (
        <p className="text-sm text-fg-muted">No positions.</p>
      ) : (
        groups.map((g) => (
          <section
            key={g.accountId}
            className="flex flex-col gap-2 rounded-lg border border-border bg-panel p-3"
            aria-label={`Positions — ${g.alias}`}
          >
            <header className="flex items-baseline justify-between">
              <h3 className="text-sm font-semibold text-fg">
                <span className="font-mono text-xs text-fg-muted">{g.broker}</span>{' '}
                {g.alias}
              </h3>
              <span className="text-xs text-fg-muted">{g.positions.length} position(s)</span>
            </header>
            <div className="h-[18rem]">
              <DataTable<Position>
                columns={columns}
                data={g.positions}
                rowKey={(p) => `${p.accountId}:${p.symbol}`}
                mobileRow={(p) => (
                  <MobileCardRow
                    primary={p.symbol}
                    secondary={`Qty ${formatQty(p.qty)}`}
                    metrics={[
                      {
                        label: 'MktVal',
                        value: (
                          <NumericCell
                            value={p.marketValue}
                            format="currency"
                            currency={p.currency}
                          />
                        ),
                      },
                      {
                        label: 'P&L',
                        value: (
                          <NumericCell
                            value={p.pnlUnrealized}
                            format="currency"
                            currency={p.currency}
                            emphasis={toneFor(p.pnlUnrealized)}
                          />
                        ),
                      },
                      {
                        label: 'Action',
                        value: (
                          <PositionTradeButton
                            position={p}
                            tradeEnabled={tradePolicies[p.accountId] ?? false}
                            maintenanceActive={maintenance.active}
                          />
                        ),
                      },
                    ]}
                  />
                )}
              />
            </div>
          </section>
        ))
      )}
      <TradeTicketModal />
    </>
  );
}

export function groupByAccount(positions: readonly Position[], accounts: readonly Account[]): Group[] {
  const byId = new Map<string, Position[]>();
  for (const p of positions) {
    const list = byId.get(p.accountId);
    if (list) {
      list.push(p);
    } else {
      byId.set(p.accountId, [p]);
    }
  }
  const sortedAccounts = [...accounts].sort((a, b) => {
    if (a.broker !== b.broker) return a.broker.localeCompare(b.broker);
    return a.id.localeCompare(b.id);
  });
  const groups: Group[] = [];
  for (const acct of sortedAccounts) {
    const list = byId.get(acct.id);
    if (!list || list.length === 0) continue;
    groups.push({
      accountId: acct.id,
      alias: acct.alias,
      broker: acct.broker,
      baseCurrency: acct.baseCurrency,
      positions: list,
    });
  }
  return groups;
}

function createPositionColumns(
  tradePolicies: Record<string, boolean>,
  maintenanceActive: boolean,
): ColumnDef<Position>[] {
  return [
    {
      accessorKey: 'symbol',
      header: 'Symbol',
      cell: (info) => <span className="font-mono text-fg">{info.getValue<string>()}</span>,
    },
    {
      accessorKey: 'qty',
      header: 'Qty',
      cell: (info) => <NumericCell value={info.getValue<number>()} format="number" digits={4} />,
    },
    {
      accessorKey: 'avgCost',
      header: 'Avg Cost',
      cell: (info) => (
        <NumericCell
          value={info.getValue<number>()}
          format="currency"
          currency={info.row.original.currency}
        />
      ),
    },
    {
      accessorKey: 'marketValue',
      header: 'Market Value',
      cell: (info) => (
        <NumericCell
          value={info.getValue<number>()}
          format="currency"
          currency={info.row.original.currency}
        />
      ),
    },
    {
      accessorKey: 'pnlUnrealized',
      header: 'P&L (Unreal.)',
      cell: (info) => {
        const v = info.getValue<number>();
        return (
          <NumericCell
            value={v}
            format="currency"
            currency={info.row.original.currency}
            emphasis={toneFor(v)}
          />
        );
      },
    },
    {
      accessorKey: 'pnlRealized',
      header: 'P&L (Real.)',
      cell: (info) => {
        const v = info.getValue<number>();
        return (
          <NumericCell
            value={v}
            format="currency"
            currency={info.row.original.currency}
            emphasis={toneFor(v)}
          />
        );
      },
    },
    {
      accessorKey: 'currency',
      header: 'Currency',
      cell: (info) => <span className="text-fg-muted">{info.getValue<string>()}</span>,
    },
    {
      id: 'trade',
      header: 'Trade',
      cell: (info) => (
        <PositionTradeButton
          position={info.row.original}
          tradeEnabled={tradePolicies[info.row.original.accountId] ?? false}
          maintenanceActive={maintenanceActive}
        />
      ),
    },
  ];
}

function PositionTradeButton({
  position,
  tradeEnabled,
  maintenanceActive,
}: {
  position: Position;
  tradeEnabled: boolean;
  maintenanceActive: boolean;
}): React.JSX.Element {
  const conid = positionConid(position);
  return (
    <TradeButton
      accountId={position.accountId}
      {...(conid === undefined ? {} : { conid })}
      symbol={position.symbol}
      tradeEnabled={tradeEnabled}
      maintenanceActive={maintenanceActive}
    />
  );
}

function positionConid(position: Position): string | undefined {
  const candidate = (position as PositionWithConid).conid;
  if (typeof candidate === 'number') return candidate.toString();
  if (typeof candidate === 'string') return candidate;
  return undefined;
}

function formatQty(n: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(n);
}

function toneFor(n: number): 'up' | 'down' | 'neutral' {
  if (n > 0) return 'up';
  if (n < 0) return 'down';
  return 'neutral';
}

function isPolicyResponse(value: unknown): value is PolicyResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    'trade_enabled' in value &&
    typeof value.trade_enabled === 'boolean'
  );
}

async function fetchTradeEnabled(accountId: string, signal: AbortSignal): Promise<boolean> {
  const response = await fetch(`/api/orders/policy/${encodeURIComponent(accountId)}`, {
    credentials: 'include',
    signal,
  });
  if (!response.ok) return false;
  const body: unknown = await response.json();
  return isPolicyResponse(body) ? body.trade_enabled : false;
}
