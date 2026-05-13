/**
 * Phase 11b-D2 — persisted alerts feed state.
 * Mirrors stores/global/ai.ts migrate-guard shape so corrupted localStorage
 * cannot hydrate malformed RecentFire entries or non-string lastSeenAt.
 */

import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

import type { RecentFire } from '@/services/alerts/types';

const FIRE_CAP = 50;

interface AlertsStore {
  recentFires: RecentFire[];
  lastSeenAt: string | null;
  appendFire: (fire: RecentFire) => void;
  mergeFires: (fires: RecentFire[]) => void;
  clear: () => void;
}

const initialState = {
  recentFires: [],
  lastSeenAt: null,
} satisfies Pick<AlertsStore, 'recentFires' | 'lastSeenAt'>;

function isRecentFire(value: unknown): value is RecentFire {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return false;
  }
  const candidate = value as {
    id?: unknown;
    alert_id?: unknown;
    fired_at?: unknown;
    verdict?: unknown;
  };
  return (
    typeof candidate.id === 'number'
    && typeof candidate.alert_id === 'number'
    && typeof candidate.fired_at === 'string'
    && typeof candidate.verdict === 'string'
  );
}

function capFires(fires: RecentFire[]): RecentFire[] {
  return fires.slice(0, FIRE_CAP);
}

function newestFiredAt(fires: RecentFire[]): string | null {
  return fires[0]?.fired_at ?? null;
}

export const useAlertsStore = create<AlertsStore>()(
  persist(
    (set, get) => ({
      ...initialState,
      appendFire: (fire: RecentFire) =>
        set(() => {
          const existing = get().recentFires;
          const deduped = existing.filter((f) => f.id !== fire.id);
          const next = capFires([fire, ...deduped]);
          return { recentFires: next, lastSeenAt: fire.fired_at };
        }),
      mergeFires: (fires: RecentFire[]) =>
        set((state) => {
          const seen = new Set(state.recentFires.map((f) => f.id));
          const additions = fires.filter((f) => !seen.has(f.id));
          const merged = capFires(
            [...additions, ...state.recentFires].sort((a, b) =>
              b.fired_at.localeCompare(a.fired_at),
            ),
          );
          return {
            recentFires: merged,
            lastSeenAt: newestFiredAt(merged) ?? state.lastSeenAt,
          };
        }),
      clear: () => set({ ...initialState }),
    }),
    {
      name: 'alerts-global',
      storage: createJSONStorage(() => localStorage),
      version: 1,
      migrate: (state: unknown) => {
        const s = state as {
          recentFires?: unknown;
          lastSeenAt?: unknown;
        } | null;
        const persistedFires = s?.recentFires;
        const persistedLastSeen = s?.lastSeenAt;
        const recentFires = Array.isArray(persistedFires)
          ? capFires(persistedFires.filter(isRecentFire))
          : [];
        if (Array.isArray(persistedFires)) {
          const dropped = persistedFires.length - recentFires.length;
          if (dropped > 0) {
            console.warn(`[alerts-store] migrate dropped ${dropped} invalid fires`);
          }
        }
        const lastSeenAt =
          typeof persistedLastSeen === 'string' || persistedLastSeen === null
            ? (persistedLastSeen ?? null)
            : null;
        if (
          persistedLastSeen !== undefined
          && persistedLastSeen !== null
          && typeof persistedLastSeen !== 'string'
        ) {
          console.warn('[alerts-store] migrate coerced invalid lastSeenAt to null');
        }
        return { recentFires, lastSeenAt };
      },
    },
  ),
);
