import * as React from 'react';
import { cn } from '@/lib/utils';

export type NumericEmphasis = 'up' | 'down' | 'neutral';

export interface NumericCellProps {
  value: number | null | undefined;
  format?: 'number' | 'currency' | 'percent';
  currency?: string;
  digits?: number;
  emphasis?: NumericEmphasis;
  className?: string;
}

function format(
  v: number,
  opts: {
    format: 'number' | 'currency' | 'percent';
    currency?: string | undefined;
    digits?: number | undefined;
  },
): string {
  const { format: f, currency = 'USD', digits = 2 } = opts;
  if (f === 'currency')
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      minimumFractionDigits: digits,
    }).format(v);
  if (f === 'percent')
    return new Intl.NumberFormat(undefined, {
      style: 'percent',
      minimumFractionDigits: digits,
    }).format(v);
  return new Intl.NumberFormat(undefined, { minimumFractionDigits: digits }).format(v);
}

export const NumericCell = React.memo(function NumericCell({
  value,
  format: f = 'number',
  currency,
  digits = 2,
  emphasis = 'neutral',
  className,
}: NumericCellProps): React.JSX.Element {
  const tone =
    emphasis === 'up' ? 'text-positive' : emphasis === 'down' ? 'text-negative' : 'text-fg';
  return (
    <span className={cn('font-mono tabular-nums text-right inline-block', tone, className)}>
      {value == null || Number.isNaN(value) ? '—' : format(value, { format: f, currency, digits })}
    </span>
  );
});
