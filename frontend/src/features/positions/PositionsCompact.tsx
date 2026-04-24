import * as React from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { NumericCell } from '@/components/primitives/NumericCell';
import { DataTable } from '@/components/patterns/DataTable';
import { MobileCardRow } from '@/components/patterns/MobileCardRow/MobileCardRow';
import { useActiveStores } from '@/stores/registry';
import type { Position } from '@/services/types';

/**
 * Compact positions list for RightPanel. Shows the currently selected
 * account's positions; if no account is selected, shows top 10 across all
 * accounts by absolute unrealized P&L.
 *
 * Reduced column set (symbol, qty, pnlUnrealized) with pnl tone-coloring.
 */
export function PositionsCompact(): React.JSX.Element {
  const { useAccounts, usePositions } = useActiveStores();
  const selectedId = useAccounts((s) => s.selectedAccountId);
  const positions = usePositions((s) => s.positions);

  const visible = React.useMemo(() => {
    if (selectedId) return positions.filter((p) => p.accountId === selectedId);
    return [...positions]
      .sort((a, b) => Math.abs(b.pnlUnrealized) - Math.abs(a.pnlUnrealized))
      .slice(0, 10);
  }, [positions, selectedId]);

  return (
    <section className="flex h-full flex-col gap-2 p-2" aria-label="Positions">
      <header className="flex items-baseline justify-between px-1">
        <h2 className="text-xs font-semibold text-fg">Positions</h2>
        <span className="text-[0.625rem] text-fg-muted">{visible.length}</span>
      </header>
      <div className="h-full min-h-0 rounded-md border border-border bg-panel">
        <DataTable<Position>
          columns={COMPACT_COLUMNS}
          data={visible}
          rowKey={(p) => `${p.accountId}:${p.symbol}`}
          rowHeight={28}
          mobileRow={(p) => (
            <MobileCardRow
              primary={p.symbol}
              secondary={`Qty ${p.qty}`}
              metrics={[
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
  );
}

function toneFor(n: number): 'up' | 'down' | 'neutral' {
  if (n > 0) return 'up';
  if (n < 0) return 'down';
  return 'neutral';
}

const COMPACT_COLUMNS: ColumnDef<Position>[] = [
  {
    accessorKey: 'symbol',
    header: 'Symbol',
    cell: (info) => (
      <span className="font-mono text-xs text-fg">{info.getValue<string>()}</span>
    ),
  },
  {
    accessorKey: 'qty',
    header: 'Qty',
    cell: (info) => (
      <NumericCell
        value={info.getValue<number>()}
        format="number"
        digits={0}
        className="text-xs"
      />
    ),
  },
  {
    accessorKey: 'pnlUnrealized',
    header: 'P&L',
    cell: (info) => {
      const v = info.getValue<number>();
      return (
        <NumericCell
          value={v}
          format="currency"
          currency={info.row.original.currency}
          emphasis={toneFor(v)}
          className="text-xs"
        />
      );
    },
  },
];
