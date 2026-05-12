import * as React from 'react';

import type { PerAccount } from '@/services/portfolio/types';

interface Props {
  accounts: PerAccount[];
}

export function PerAccountTable({ accounts }: Props): React.JSX.Element {
  return (
    <section
      className="rounded-md border border-border bg-panel p-4"
      data-testid="rollup-per-account-table"
    >
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        Per account
      </h2>
      {accounts.length === 0 ? (
        <div className="text-sm text-muted-foreground">No accounts</div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase text-muted-foreground">
              <th className="py-2 pr-2">Alias</th>
              <th className="py-2 pr-2">NLV (native)</th>
              <th className="py-2 pr-2">NLV (base)</th>
              <th className="py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => (
              <tr
                key={a.account_id}
                className="border-b border-border last:border-0"
                data-testid={`rollup-account-row-${a.account_id}`}
              >
                <td className="py-2 pr-2">
                  {a.alias}{' '}
                  <span className="text-xs text-muted-foreground">
                    ({a.broker_id})
                  </span>
                </td>
                <td className="py-2 pr-2 tabular-nums">
                  {a.nlv_native} {a.currency_native}
                </td>
                <td className="py-2 pr-2 tabular-nums">
                  {a.nlv_base ?? '—'}
                </td>
                <td className="py-2">
                  <StatusBadge status={a.status} fxStale={a.fx_stale} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function StatusBadge({
  status,
  fxStale,
}: {
  status: PerAccount['status'];
  fxStale: boolean;
}): React.JSX.Element {
  if (fxStale) {
    return (
      <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-900">
        fx-stale
      </span>
    );
  }
  const color =
    status === 'live'
      ? 'bg-green-100 text-green-800'
      : status === 'stale'
        ? 'bg-amber-100 text-amber-900'
        : 'bg-gray-100 text-gray-700';
  return <span className={`rounded px-2 py-0.5 text-xs ${color}`}>{status}</span>;
}
