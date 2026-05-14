import { createFileRoute } from '@tanstack/react-router';

import { OptionChainPage } from '@/features/options/OptionChainPage';

function asString(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined;
}

export interface OptionChainSearch {
  symbol: string | undefined;
  expiry: string | undefined;
}

function validateSearch(search: Record<string, unknown>): OptionChainSearch {
  return {
    symbol: asString(search.symbol),
    expiry: asString(search.expiry),
  };
}

export const Route = createFileRoute('/options/chain')({
  component: OptionChainPage,
  validateSearch,
});
