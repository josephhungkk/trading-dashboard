import { create } from 'zustand';
import type { Mode, Watchlist } from '@/services/types';
import type { Services } from '@/services/registry';
import type { Scoped } from './types';

export interface WatchlistsState {
  watchlists: Watchlist[];
  activeWatchlistId: string | null;
  svc: Services | null;
  hydrate(svc: Services): Promise<void>;
  suspend(): void;
  upsert(wl: Watchlist): Promise<void>;
  remove(id: string): Promise<void>;
  setActive(id: string | null): void;
}

export function createWatchlistsStore<M extends Mode>(_mode: M) {
  void _mode;
  const store = create<WatchlistsState>((set, get) => ({
    watchlists: [],
    activeWatchlistId: null,
    svc: null,
    async hydrate(svc) {
      const lists = await svc.watchlists.list();
      set({
        watchlists: lists,
        activeWatchlistId: lists[0]?.id ?? null,
        svc,
      });
    },
    suspend() { set({ watchlists: [], activeWatchlistId: null, svc: null }); },
    async upsert(wl) {
      const state = get();
      const idx = state.watchlists.findIndex(w => w.id === wl.id);
      const next = idx >= 0
        ? state.watchlists.map(w => w.id === wl.id ? wl : w)
        : [...state.watchlists, wl];
      set({ watchlists: next });
      if (state.svc) await state.svc.watchlists.save(next);
    },
    async remove(id) {
      const state = get();
      const next = state.watchlists.filter(w => w.id !== id);
      set({
        watchlists: next,
        activeWatchlistId: state.activeWatchlistId === id ? (next[0]?.id ?? null) : state.activeWatchlistId,
      });
      if (state.svc) await state.svc.watchlists.save(next);
    },
    setActive(id) { set({ activeWatchlistId: id }); },
  }));
  return store as unknown as Scoped<M, typeof store>;
}
