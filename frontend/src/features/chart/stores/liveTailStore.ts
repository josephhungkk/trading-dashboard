import { create } from 'zustand';

const FINAL_REVISION = 2 ** 31 - 1;

export interface LiveTailState {
  // map per (canonical, tf): bucket_start ISO -> last_seen_revision
  lastSeen: Map<string, Map<string, number>>;
  lockedBuckets: Set<string>; // "<canonical>|<tf>|<bucket_start>"
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
  lockedBuckets: new Set(),
  shouldAccept: (canonical, tf, bucketStart, revision) => {
    const key = `${canonical}|${tf}`;
    const lockKey = `${key}|${bucketStart}`;
    if (get().lockedBuckets.has(lockKey)) return false;
    const inner = get().lastSeen.get(key);
    if (!inner) return true;
    const last = inner.get(bucketStart);
    return last === undefined || revision > last;
  },
  recordSeen: (canonical, tf, bucketStart, revision) =>
    set((s) => {
      const key = `${canonical}|${tf}`;
      const inner = new Map(s.lastSeen.get(key) ?? []);
      inner.set(bucketStart, revision);
      const newMap = new Map(s.lastSeen);
      newMap.set(key, inner);
      return { lastSeen: newMap };
    }),
  lockBucket: (canonical, tf, bucketStart) =>
    set((s) => ({
      lockedBuckets: new Set(s.lockedBuckets).add(`${canonical}|${tf}|${bucketStart}`),
    })),
}));

export const FINAL_REVISION_VAL = FINAL_REVISION;
