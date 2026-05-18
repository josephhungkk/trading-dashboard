import * as React from 'react';
import type { FutureContractMonth } from '@/services/futures/types';

interface Props {
  contract: FutureContractMonth;
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export function FutureDetailsSection({ contract }: Props): React.JSX.Element {
  const settlementColor =
    contract.settlement_type === 'PHYSICAL' ? 'text-amber-600' : 'text-muted-foreground';

  return (
    <div className="rounded-md border border-border p-3 space-y-2 text-sm" data-testid="future-details-section">
      <div className="font-semibold text-foreground">Futures Contract</div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        <span className="text-muted-foreground">Contract month</span>
        <span className="font-mono">{contract.contract_month}</span>

        <span className="text-muted-foreground">Expiry</span>
        <span className="font-mono">{formatDate(contract.expiry)}</span>

        <span className="text-muted-foreground">Exchange</span>
        <span>{contract.exchange}</span>

        <span className="text-muted-foreground">Multiplier</span>
        <span className="font-mono">{contract.multiplier}</span>

        <span className="text-muted-foreground">Tick size</span>
        <span className="font-mono">{contract.tick_size}</span>

        <span className="text-muted-foreground">Tick value</span>
        <span className="font-mono">${contract.tick_value}</span>

        <span className="text-muted-foreground">Settlement</span>
        <span className={settlementColor}>{contract.settlement_type}</span>

        {contract.first_notice_day != null && (
          <>
            <span className="text-muted-foreground">First notice day</span>
            <span className="font-mono text-amber-600">{formatDate(contract.first_notice_day)}</span>
          </>
        )}

        <span className="text-muted-foreground">Days to expiry</span>
        <span
          className={contract.days_to_expiry <= 5 ? 'text-red-600 font-semibold' : ''}
        >
          {contract.days_to_expiry}
        </span>
      </div>

      {contract.settlement_type === 'PHYSICAL' && (
        <p className="text-xs text-amber-600 mt-1">
          Physical delivery — ensure you close before first notice day or the risk gate will block new opens.
        </p>
      )}
    </div>
  );
}
