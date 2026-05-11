import { createFileRoute } from '@tanstack/react-router';

import { SizingCalculatorPage } from '@/features/sizing/SizingCalculatorPage';

export interface SizingSearch {
  account_id: string | undefined;
  instrument_id: number | undefined;
  side: 'buy' | 'sell';
  entry: string | undefined;
  stop: string | undefined;
}

function asString(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined;
}

function asInstrumentId(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === 'string' && value !== '') {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function asSide(value: unknown): 'buy' | 'sell' {
  return value === 'sell' ? 'sell' : 'buy';
}

function validateSearch(search: Record<string, unknown>): SizingSearch {
  return {
    account_id: asString(search.account_id),
    instrument_id: asInstrumentId(search.instrument_id),
    side: asSide(search.side),
    entry: asString(search.entry),
    stop: asString(search.stop),
  };
}

export const Route = createFileRoute('/trade/sizing')({
  component: SizingCalculatorPage,
  validateSearch,
});
