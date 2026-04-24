import { create } from 'zustand';
import type { Mode, Position } from '@/services/types';
import type { Services } from '@/services/registry';
import type { Scoped } from './types';

export interface PositionsState {
  positions: Position[];
  hydrate(svc: Services): Promise<void>;
  suspend(): void;
}

export function createPositionsStore<M extends Mode>(mode: M) {
  const store = create<PositionsState>((set) => ({
    positions: [],
    async hydrate(svc) {
      const pos = await svc.positions.list(mode);
      set({ positions: pos });
    },
    suspend() { set({ positions: [] }); },
  }));
  return store as unknown as Scoped<M, typeof store>;
}
