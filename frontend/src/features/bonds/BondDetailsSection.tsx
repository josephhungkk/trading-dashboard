import * as React from 'react';
import type { BondInstrument } from '@/services/bonds/types';

interface Props {
  bond: BondInstrument;
  accrued?: string | null;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function couponFreqLabel(freq: number | undefined): string {
  switch (freq) {
    case 0: return 'Zero coupon';
    case 1: return 'Annual';
    case 2: return 'Semi-annual';
    case 4: return 'Quarterly';
    case 12: return 'Monthly';
    default: return freq != null ? String(freq) : '—';
  }
}

export function BondDetailsSection({ bond, accrued }: Props): React.JSX.Element {
  const { meta } = bond;
  const isCallable = meta.callable === true;

  return (
    <div
      className="rounded-md border border-border p-3 space-y-2 text-sm"
      data-testid="bond-details-section"
    >
      <div className="font-semibold text-foreground">Bond Details</div>

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

        <span className="text-muted-foreground">Bond type</span>
        <span>{meta.bond_type ?? '—'}</span>

        <span className="text-muted-foreground">Coupon rate</span>
        <span className="font-mono">{meta.coupon_rate != null ? `${meta.coupon_rate}%` : '—'}</span>

        <span className="text-muted-foreground">Frequency</span>
        <span>{couponFreqLabel(meta.coupon_frequency)}</span>

        <span className="text-muted-foreground">Maturity</span>
        <span className="font-mono">{formatDate(meta.maturity_date)}</span>

        <span className="text-muted-foreground">Face value</span>
        <span className="font-mono">{meta.face_value ?? '—'}</span>

        <span className="text-muted-foreground">Currency</span>
        <span>{bond.currency}</span>

        {meta.credit_rating && (
          <>
            <span className="text-muted-foreground">Rating</span>
            <span className="font-mono">{meta.credit_rating}</span>
          </>
        )}
        {meta.yield_to_maturity && (
          <>
            <span className="text-muted-foreground">YTM</span>
            <span className="font-mono">{meta.yield_to_maturity}%</span>
          </>
        )}
        {meta.duration && (
          <>
            <span className="text-muted-foreground">Duration</span>
            <span className="font-mono">{meta.duration} yrs</span>
          </>
        )}

        <span className="text-muted-foreground">Settlement days</span>
        <span>{meta.settlement_days ?? 2}</span>

        <span className="text-muted-foreground">Callable</span>
        <span className={isCallable ? 'text-amber-600' : 'text-muted-foreground'}>
          {isCallable ? 'Yes' : 'No'}
        </span>

        {accrued != null && (
          <>
            <span className="text-muted-foreground">Accrued interest</span>
            <span className="font-mono">{accrued}</span>
          </>
        )}
      </div>

      {isCallable && (
        <p className="text-xs text-amber-600 mt-1" role="alert">
          Callable bond — issuer may redeem before maturity.
        </p>
      )}
    </div>
  );
}
