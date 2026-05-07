import { create } from 'zustand';

const FINAL_REVISION = 2 ** 31 - 1;

// MED-2: use nested Maps to avoid separator collision with '|' in canonical IDs or timeframes.
// Structure: canonical → tf → bucket_start → last_seen_revision
export interface LiveTailState {
  lastSeen: Map<string, Map<string, Map<string, number>>>;
  // canonical → tf → Set<bucket_start>
  lockedBuckets: Map<string, Map<string, Set<string>>>;
  shouldAccept: (
    canonical: string,
    tf: string,
    bucketStart: string,
    revision: number,
  ) => boolean;
  recordSeen: (canonical: string, tf: string, bucketStart: string, revision: number) => void;
  lockBucket: (canonical: string, tf: string, bucketStart: string) => void;
}

export const useLiveTailStore = create<LiveTailState>((set, get) => ({
  lastSeen: new Map(),
  lockedBuckets: new Map(),

  shouldAccept: (canonical, tf, bucketStart, revision) => {
    // Check lockedBuckets nested Map
    const lockedByCanon = get().lockedBuckets.get(canonical);
    if (lockedByCanon?.get(tf)?.has(bucketStart)) return false;

    // Check lastSeen nested Map
    const last = get().lastSeen.get(canonical)?.get(tf)?.get(bucketStart);
    return last === undefined || revision > last;
  },

  recordSeen: (canonical, tf, bucketStart, revision) =>
    set((s) => {
      // Immutably update lastSeen: canonical → tf → bucket_start → revision
      const outerMap = new Map(s.lastSeen);
      const tfMap = new Map(outerMap.get(canonical) ?? []);
      const bucketMap = new Map(tfMap.get(tf) ?? []);
      bucketMap.set(bucketStart, revision);
      tfMap.set(tf, bucketMap);
      outerMap.set(canonical, tfMap);
      return { lastSeen: outerMap };
    }),

  lockBucket: (canonical, tf, bucketStart) =>
    set((s) => {
      // Immutably update lockedBuckets: canonical → tf → Set<bucket_start>
      const outerMap = new Map(s.lockedBuckets);
      const tfMap = new Map(outerMap.get(canonical) ?? []);
      const bucketSet = new Set(tfMap.get(tf) ?? []);
      bucketSet.add(bucketStart);
      tfMap.set(tf, bucketSet);
      outerMap.set(canonical, tfMap);
      return { lockedBuckets: outerMap };
    }),
}));

export const FINAL_REVISION_VAL = FINAL_REVISION;
