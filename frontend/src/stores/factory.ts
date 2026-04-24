import type { Mode } from '@/services/types';
import type { Services } from '@/services/registry';
import { createAccountStore } from './scoped/account-store';
import { createPositionsStore } from './scoped/positions-store';
import { createOrdersStore } from './scoped/orders-store';
import { createWatchlistsStore } from './scoped/watchlists-store';

export interface ScopedStores<M extends Mode> {
  readonly mode: M;
  useAccounts:   ReturnType<typeof createAccountStore<M>>;
  usePositions:  ReturnType<typeof createPositionsStore<M>>;
  useOrders:     ReturnType<typeof createOrdersStore<M>>;
  useWatchlists: ReturnType<typeof createWatchlistsStore<M>>;
  hydrate(svc: Services): Promise<void>;
  suspend(): void;
}

export function createScopedStores<M extends Mode>(mode: M): ScopedStores<M> {
  const useAccounts   = createAccountStore(mode);
  const usePositions  = createPositionsStore(mode);
  const useOrders     = createOrdersStore(mode);
  const useWatchlists = createWatchlistsStore(mode);
  return {
    mode,
    useAccounts, usePositions, useOrders, useWatchlists,
    async hydrate(svc) {
      await Promise.all([
        useAccounts.getState().hydrate(svc),
        usePositions.getState().hydrate(svc),
        useOrders.getState().hydrate(svc),
        useWatchlists.getState().hydrate(svc),
      ]);
    },
    suspend() {
      useAccounts.getState().suspend();
      usePositions.getState().suspend();
      useOrders.getState().suspend();
      useWatchlists.getState().suspend();
    },
  };
}
