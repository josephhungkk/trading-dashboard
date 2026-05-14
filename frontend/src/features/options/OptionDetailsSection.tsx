import * as React from 'react';
import type { OptionChainRow } from './types';
import { OptionGreeksStrip } from './OptionGreeksStrip';

interface Props {
  row: OptionChainRow;
  underlyingSymbol: string;
  expiryIso: string;
  onSideChange: (side: 'BUY' | 'SELL', positionEffect: 'OPEN' | 'CLOSE') => void;
}

function formatExpiry(iso: string): string {
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function tradingDaysUntil(iso: string): number {
  const now = new Date();
  const expiry = new Date(iso + 'T00:00:00');
  let days = 0;
  const cur = new Date(now);
  while (cur < expiry) {
    cur.setDate(cur.getDate() + 1);
    if (cur.getDay() !== 0 && cur.getDay() !== 6) days++;
  }
  return days;
}

type LegCode = 'BTO' | 'STO' | 'BTC' | 'STC';

const LEG_LABELS: Record<LegCode, string> = {
  BTO: 'Buy to Open',
  STO: 'Sell to Open',
  BTC: 'Buy to Close',
  STC: 'Sell to Close',
};

const LEG_MAP: Record<LegCode, { side: 'BUY' | 'SELL'; pe: 'OPEN' | 'CLOSE' }> = {
  BTO: { side: 'BUY', pe: 'OPEN' },
  STO: { side: 'SELL', pe: 'OPEN' },
  BTC: { side: 'BUY', pe: 'CLOSE' },
  STC: { side: 'SELL', pe: 'CLOSE' },
};

export function OptionDetailsSection({ row, underlyingSymbol, expiryIso, onSideChange }: Props) {
  const [selectedLeg, setSelectedLeg] = React.useState<LegCode>('BTO');
  const isZeroDte = expiryIso === new Date().toISOString().slice(0, 10);
  const tradingDays = tradingDaysUntil(expiryIso);
  const styleLabel = row.style === 'A' ? 'American' : 'European';
  const premium = ((parseFloat(row.bid) + parseFloat(row.ask)) / 2).toFixed(2);
  const notional = (parseFloat(premium) * row.multiplier).toFixed(2);

  function handleLegSelect(leg: LegCode) {
    setSelectedLeg(leg);
    onSideChange(LEG_MAP[leg].side, LEG_MAP[leg].pe);
  }

  return (
    <div className="rounded-md border border-border p-3 space-y-2" data-testid="option-details-section">
      <div>
        <div className="font-semibold text-sm">
          {underlyingSymbol} {formatExpiry(expiryIso)}{' '}
          <span className={row.put_call === 'C' ? 'text-green-400' : 'text-red-400'}>
            {row.strike}
            {row.put_call === 'C' ? 'C' : 'P'}
          </span>
        </div>
        <div className="text-xs text-muted-foreground">
          {styleLabel} · ×{row.multiplier} · {row.exchange} · expires in {tradingDays} trading days
        </div>
      </div>

      <OptionGreeksStrip
        greeks={{ delta: row.delta, gamma: row.gamma, theta: row.theta, vega: row.vega, iv: row.iv }}
      />

      <div className="text-xs text-muted-foreground border-t border-border pt-2">
        Premium {premium} · Notional per contract{' '}
        <strong className="text-foreground">${notional}</strong> · 1 contract = {row.multiplier}{' '}
        shares {underlyingSymbol}
      </div>

      <div className="flex gap-1 flex-wrap">
        {(['BTO', 'STO', 'BTC', 'STC'] as const).map((leg) => (
          <button
            key={leg}
            onClick={() => handleLegSelect(leg)}
            className={`text-xs rounded border px-2 py-0.5 transition-colors ${
              leg === selectedLeg
                ? 'bg-accent text-accent-foreground border-accent'
                : 'border-border text-muted-foreground hover:border-foreground'
            }`}
            data-testid={`leg-select-${leg}`}
          >
            {LEG_LABELS[leg]}
          </button>
        ))}
      </div>

      {isZeroDte && (
        <div
          className="rounded bg-yellow-400/10 border border-yellow-400/40 px-2 py-1 text-xs text-yellow-400"
          role="alert"
          data-testid="zero-dte-banner"
        >
          ⚠ This option expires today (0DTE). Exercise settlement risk applies.
        </div>
      )}
    </div>
  );
}
