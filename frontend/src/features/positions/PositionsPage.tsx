import * as React from 'react';
import { useActiveStores } from '@/stores/registry';
import { PositionsTable } from './PositionsTable';

/**
 * Positions page — positions grouped by (broker, accountId). DataTable does
 * not currently support group headers, so we render one `<section>` per
 * account with an account alias heading, each containing its own DataTable.
 */
export function PositionsPage(): React.JSX.Element {
  const { useAccounts, usePositions } = useActiveStores();
  const accounts = useAccounts((s) => s.accounts);
  const positions = usePositions((s) => s.positions);
  const activeAccountIds = React.useMemo(
    () => new Set(positions.map((position) => position.accountId)),
    [positions],
  );
  const groupCount = accounts.filter((account) => activeAccountIds.has(account.id)).length;

  return (
    <div className="flex h-full flex-col gap-4 p-4" aria-label="Positions">
      <header className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold text-fg">Positions</h2>
        <p className="text-xs text-fg-muted">
          {positions.length} position(s) across {groupCount} account(s)
        </p>
      </header>

      <PositionsTable />
    </div>
  );
}
