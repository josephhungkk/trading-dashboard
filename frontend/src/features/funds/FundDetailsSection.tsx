import * as React from 'react';
import type { FundInstrument } from '@/services/funds/types';

interface Props {
  fund: FundInstrument;
  latestNav?: string | null;
  navDate?: string | null;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export function FundDetailsSection({ fund, latestNav, navDate }: Props): React.JSX.Element {
  const { meta } = fund;

  return (
    <div
      className="rounded-md border border-border p-3 space-y-2 text-sm"
      data-testid="fund-details-section"
    >
      <div className="font-semibold text-foreground">Mutual Fund Details</div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        {meta.isin && (
          <>
            <span className="text-muted-foreground">ISIN</span>
            <span className="font-mono">{meta.isin}</span>
          </>
        )}
        {meta.cusip && (
          <>
            <span className="text-muted-foreground">CUSIP</span>
            <span className="font-mono">{meta.cusip}</span>
          </>
        )}

        <span className="text-muted-foreground">Fund family</span>
        <span>{meta.fund_family ?? '—'}</span>

        <span className="text-muted-foreground">Fund type</span>
        <span>{meta.fund_type ?? '—'}</span>

        <span className="text-muted-foreground">Currency</span>
        <span>{fund.currency}</span>

        <span className="text-muted-foreground">NAV currency</span>
        <span>{meta.nav_currency ?? '—'}</span>

        <span className="text-muted-foreground">Min investment</span>
        <span className="font-mono">{meta.min_investment ?? '—'}</span>

        <span className="text-muted-foreground">Min subsequent</span>
        <span className="font-mono">{meta.min_subsequent ?? '—'}</span>

        <span className="text-muted-foreground">Fractional</span>
        <span className={meta.allows_fractional ? 'text-green-600' : 'text-muted-foreground'}>
          {meta.allows_fractional ? 'Yes' : 'No'}
        </span>

        {meta.cutoff_time_et && (
          <>
            <span className="text-muted-foreground">Cut-off (ET)</span>
            <span className="font-mono">{meta.cutoff_time_et}</span>
          </>
        )}
        {meta.expense_ratio && (
          <>
            <span className="text-muted-foreground">Expense ratio</span>
            <span className="font-mono">{meta.expense_ratio}%</span>
          </>
        )}

        <span className="text-muted-foreground">Settlement days</span>
        <span>{meta.settlement_days ?? 1}</span>

        {latestNav != null && (
          <>
            <span className="text-muted-foreground">Latest NAV</span>
            <span className="font-mono">{latestNav}</span>
          </>
        )}
        {navDate != null && (
          <>
            <span className="text-muted-foreground">NAV date</span>
            <span className="font-mono">{formatDate(navDate)}</span>
          </>
        )}
      </div>
    </div>
  );
}
