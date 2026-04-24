import * as React from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { NumericCell } from '@/components/primitives/NumericCell';
import { DataTable } from '@/components/patterns/DataTable';
import { MobileCardRow } from '@/components/patterns/MobileCardRow/MobileCardRow';
import { useActiveStores } from '@/stores/registry';
import type { Account, Position } from '@/services/types';

interface Group {
  accountId: string;
  alias: string;
  broker: string;
  baseCurrency: string;
  positions: Position[];
}

/**
 * Positions page — positions grouped by (broker, accountId). DataTable does
 * not currently support group headers, so we render one `<section>` per
 * account with an account alias heading, each containing its own DataTable.
 */
export function PositionsPage(): React.JSX.Element {
  const { useAccounts, usePositions } = useActiveStores();
  const accounts = useAccounts((s) => s.accounts);
  const positions = usePositions((s) => s.positions);

  const groups = React.useMemo(() => groupByAccount(positions, accounts), [positions, accounts]);

  return (
    <div className="flex h-full flex-col gap-4 p-4" aria-label="Positions">
      <header className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold text-fg">Positions</h2>
        <p className="text-xs text-fg-muted">
          {positions.length} position(s) across {groups.length} account(s)
        </p>
      </header>

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
                columns={POSITION_COLUMNS}
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
                    ]}
                  />
                )}
              />
            </div>
          </section>
        ))
      )}
    </div>
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

function formatQty(n: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(n);
}

function toneFor(n: number): 'up' | 'down' | 'neutral' {
  if (n > 0) return 'up';
  if (n < 0) return 'down';
  return 'neutral';
}

const POSITION_COLUMNS: ColumnDef<Position>[] = [
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
];
