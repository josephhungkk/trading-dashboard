import * as React from 'react';

import { usePositionSizing } from '@/services/sizing/usePositionSizing';
import type {
  SizingInputs,
  SizingMethod,
  SizingRequest,
} from '@/services/sizing/types';

interface Props {
  method: SizingMethod;
  accountId: string | undefined;
  instrumentId: number | undefined;
  side: 'buy' | 'sell';
  entry: string | undefined;
  stop: string | undefined;
}

const METHOD_LABEL: Record<SizingMethod, string> = {
  fixed_fractional: 'Fixed-fractional',
  risk_per_trade: 'Risk-per-trade',
  vol_targeted: 'Vol-targeted',
};

export function SizingMethodColumn({
  method,
  accountId,
  instrumentId,
  side,
  entry,
  stop,
}: Props): React.JSX.Element {
  const defaultPct =
    method === 'vol_targeted'
      ? '15.00'
      : method === 'risk_per_trade'
        ? '1.00'
        : '2.00';
  const [riskPct, setRiskPct] = React.useState(defaultPct);

  const inputs: SizingInputs | null = entry
    ? method === 'fixed_fractional'
      ? { kind: 'fixed_fractional', risk_pct: riskPct, price: entry }
      : method === 'risk_per_trade'
        ? {
            kind: 'risk_per_trade',
            risk_pct: riskPct,
            entry,
            stop: stop ?? '0',
          }
        : { kind: 'vol_targeted', target_vol_pct: riskPct, price: entry }
    : null;

  const req: SizingRequest | null =
    accountId && instrumentId && inputs
      ? {
          account_id: accountId,
          instrument_id: instrumentId,
          method,
          side,
          inputs,
        }
      : null;

  const sizing = usePositionSizing(req);

  return (
    <div
      className="rounded-md border border-border p-4"
      data-testid={`column-${method}`}
    >
      <h2 className="text-sm font-semibold">{METHOD_LABEL[method]}</h2>
      <label className="mt-3 block text-xs">
        {method === 'vol_targeted' ? 'Target vol %' : 'Risk %'}:
        <input
          type="text"
          inputMode="decimal"
          value={riskPct}
          onChange={(e) => setRiskPct(e.currentTarget.value)}
          className="mt-1 w-full rounded-md border border-border bg-panel p-2"
          data-testid={`risk-pct-${method}`}
        />
      </label>
      {sizing.loading ? (
        <div className="mt-3 text-xs text-muted-foreground">Computing…</div>
      ) : null}
      {sizing.result ? (
        <div className="mt-3 text-sm">
          <div>
            Suggested qty:{' '}
            <span
              className="font-semibold"
              data-testid={`qty-${method}`}
            >
              {sizing.result.suggested_qty}
            </span>
          </div>
          <div className="text-xs text-muted-foreground">
            Notional: {sizing.result.base_currency_notional}{' '}
            {sizing.result.breakdown.account_currency}
          </div>
          <div className="mt-2 text-xs">
            Gate verdict:{' '}
            <span data-testid={`verdict-${method}`}>
              {sizing.result.risk_verdict.final_verdict.toUpperCase()}
            </span>
          </div>
        </div>
      ) : null}
      {sizing.error ? (
        <div
          className="mt-3 text-xs text-destructive"
          data-testid={`error-${method}`}
        >
          {sizing.error.message}
        </div>
      ) : null}
    </div>
  );
}
