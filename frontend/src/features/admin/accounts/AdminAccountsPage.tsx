/**
 * Phase 10a E5 (wire-up) — admin accounts table embedding the
 * per-account kill-switch row. Reuses the same TanStack Query
 * + ['admin-accounts', mode] key so other admin surfaces (e.g.
 * NLV monitor) can share the fetch.
 *
 * Lives at /admin/accounts (route file: routes/admin.accounts.tsx).
 */

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchAccountsAndSyncMaintenance } from '@/hooks/useAccountsList';
import type { Account, Mode } from '@/services/types';
import { AccountKillSwitchRow } from '@/features/admin/accounts/AccountKillSwitchRow';

const ADMIN_MODE: Mode = 'paper';

export function AdminAccountsPage(): React.JSX.Element {
  const query = useQuery<Account[], Error>({
    queryKey: ['admin-accounts', ADMIN_MODE],
    queryFn: () => fetchAccountsAndSyncMaintenance(ADMIN_MODE),
    staleTime: 30_000,
  });

  return (
    <section className="flex flex-col gap-4 p-4">
      <header className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold">Accounts</h1>
        <p className="text-sm text-fg-muted">
          Per-account kill-switch toggle freezes trading at the risk gate.
          History is captured in the audit trail.
        </p>
      </header>

      {query.isLoading ? (
        <p className="text-sm text-fg-muted">Loading accounts…</p>
      ) : query.error ? (
        <p role="alert" className="text-sm text-destructive">{query.error.message}</p>
      ) : query.data && query.data.length === 0 ? (
        <p className="text-sm text-fg-muted">No accounts configured.</p>
      ) : (
        <div className="overflow-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-panel-muted text-left text-fg-muted">
              <tr>
                <th className="p-2">Alias</th>
                <th className="p-2">Broker</th>
                <th className="p-2">Mode</th>
                <th className="p-2">Base ccy</th>
                <th className="p-2">Kill switch</th>
              </tr>
            </thead>
            <tbody>
              {query.data?.map((account) => (
                <tr key={account.id} className="border-t border-border">
                  <td className="p-2 font-medium">{account.alias}</td>
                  <td className="p-2">{account.broker}</td>
                  <td className="p-2">{account.mode}</td>
                  <td className="p-2">{account.baseCurrency}</td>
                  <td className="p-2">
                    <AccountKillSwitchRow
                      accountId={account.id}
                      accountLabel={account.alias}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
