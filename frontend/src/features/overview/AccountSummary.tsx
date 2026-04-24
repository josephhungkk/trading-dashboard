import * as React from 'react';
import { NumericCell } from '@/components/primitives/NumericCell';
import { useActiveStores } from '@/stores/registry';

/**
 * Compact card shown in the LeftPanel. Renders the currently-selected account's
 * alias, account number, NLV and today's P&L (derived from positions —
 * pnlToday is not yet tracked, so we sum pnlUnrealized across the account's
 * positions as a stand-in).
 */
export function AccountSummary(): React.JSX.Element {
  const { useAccounts, usePositions } = useActiveStores();
  const accounts = useAccounts((s) => s.accounts);
  const selectedId = useAccounts((s) => s.selectedAccountId);
  const positions = usePositions((s) => s.positions);

  const selected = accounts.find((a) => a.id === selectedId) ?? null;

  if (!selected) {
    return (
      <section className="flex h-full flex-col gap-2 p-4">
        <h2 className="text-sm font-semibold text-fg">Account Summary</h2>
        <p className="text-xs text-fg-muted">Select an account</p>
      </section>
    );
  }

  const accountPositions = positions.filter((p) => p.accountId === selected.id);
  // NLV prefers the account's canonical NLV field; fall back to sum of
  // marketValue if the adapter hasn't populated it yet.
  const nlv =
    typeof selected.nlv === 'number' && !Number.isNaN(selected.nlv)
      ? selected.nlv
      : accountPositions.reduce((acc, p) => acc + p.marketValue, 0);
  // "Today's P&L" stand-in — pnlToday isn't modeled yet.
  const pnlToday = accountPositions.reduce((acc, p) => acc + p.pnlUnrealized, 0);
  const pnlEmphasis: 'up' | 'down' | 'neutral' =
    pnlToday > 0 ? 'up' : pnlToday < 0 ? 'down' : 'neutral';

  return (
    <section
      className="flex h-full flex-col gap-2 p-4"
      aria-label="Account summary"
    >
      <h2 className="text-sm font-semibold text-fg">Account Summary</h2>

      <dl className="flex flex-col text-xs">
        <div className="flex items-baseline justify-between gap-2 border-b border-border py-2">
          <dt className="text-fg-muted">Alias</dt>
          <dd className="truncate text-fg">{selected.alias}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-2 border-b border-border py-2">
          <dt className="text-fg-muted">Account</dt>
          <dd className="font-mono text-fg">{selected.accountNumber}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-2 border-b border-border py-2">
          <dt className="text-fg-muted">NLV</dt>
          <dd>
            <NumericCell value={nlv} format="currency" currency={selected.baseCurrency} />
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-2 py-2">
          <dt className="text-fg-muted">Today&apos;s P&amp;L</dt>
          <dd>
            <NumericCell
              value={pnlToday}
              format="currency"
              currency={selected.baseCurrency}
              emphasis={pnlEmphasis}
            />
          </dd>
        </div>
      </dl>
    </section>
  );
}
