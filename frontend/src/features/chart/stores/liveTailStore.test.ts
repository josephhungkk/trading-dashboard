import { describe, it, expect, beforeEach } from 'vitest';
import { useLiveTailStore, FINAL_REVISION_VAL } from './liveTailStore';

function resetStore(): void {
  useLiveTailStore.setState({
    lastSeen: new Map(),
    lockedBuckets: new Map(),
  });
}

const CANONICAL = 'AAPL.US';
const TF = '1m';
const BUCKET = '2026-05-07T14:00:00Z';

describe('useLiveTailStore', () => {
  beforeEach(() => {
    resetStore();
  });

  it('shouldAccept returns true for unknown bucket', () => {
    expect(useLiveTailStore.getState().shouldAccept(CANONICAL, TF, BUCKET, 1)).toBe(true);
  });

  it('shouldAccept returns false for equal revision (not strictly greater)', () => {
    const { recordSeen, shouldAccept } = useLiveTailStore.getState();
    recordSeen(CANONICAL, TF, BUCKET, 5);
    expect(shouldAccept(CANONICAL, TF, BUCKET, 5)).toBe(false);
  });

  it('shouldAccept returns false for lower revision (out-of-order)', () => {
    const { recordSeen, shouldAccept } = useLiveTailStore.getState();
    recordSeen(CANONICAL, TF, BUCKET, 5);
    expect(shouldAccept(CANONICAL, TF, BUCKET, 3)).toBe(false);
  });

  it('shouldAccept returns true for higher revision', () => {
    const { recordSeen, shouldAccept } = useLiveTailStore.getState();
    recordSeen(CANONICAL, TF, BUCKET, 5);
    expect(shouldAccept(CANONICAL, TF, BUCKET, 6)).toBe(true);
  });

  it('lockBucket prevents all future accepts for that bucket', () => {
    const { lockBucket, shouldAccept } = useLiveTailStore.getState();
    lockBucket(CANONICAL, TF, BUCKET);
    expect(shouldAccept(CANONICAL, TF, BUCKET, FINAL_REVISION_VAL)).toBe(false);
  });

  it('lockBucket does not affect other buckets', () => {
    const OTHER_BUCKET = '2026-05-07T14:01:00Z';
    useLiveTailStore.getState().lockBucket(CANONICAL, TF, BUCKET);
    expect(useLiveTailStore.getState().shouldAccept(CANONICAL, TF, OTHER_BUCKET, 1)).toBe(true);
  });

  it('FINAL_REVISION_VAL equals 2^31 - 1', () => {
    expect(FINAL_REVISION_VAL).toBe(2 ** 31 - 1);
  });

  // MED-2: nested Map structure tests — verify the store uses canonical → tf → bucket nesting
  // to avoid separator collision (e.g. a canonical_id containing '|' would collide with
  // the old flat-string key scheme).

  describe('MED-2: nested Map structure', () => {
    it('lastSeen uses nested Map: canonical → tf → bucket_start → revision', () => {
      const { recordSeen } = useLiveTailStore.getState();
      recordSeen(CANONICAL, TF, BUCKET, 7);

      const { lastSeen } = useLiveTailStore.getState();
      const canonMap = lastSeen.get(CANONICAL);
      expect(canonMap).toBeInstanceOf(Map);
      const tfMap = canonMap?.get(TF);
      expect(tfMap).toBeInstanceOf(Map);
      expect(tfMap?.get(BUCKET)).toBe(7);
    });

    it('lockedBuckets uses nested Map: canonical → tf → Set<bucket_start>', () => {
      const { lockBucket } = useLiveTailStore.getState();
      lockBucket(CANONICAL, TF, BUCKET);

      const { lockedBuckets } = useLiveTailStore.getState();
      const canonMap = lockedBuckets.get(CANONICAL);
      expect(canonMap).toBeInstanceOf(Map);
      const bucketSet = canonMap?.get(TF);
      expect(bucketSet).toBeInstanceOf(Set);
      expect(bucketSet?.has(BUCKET)).toBe(true);
    });

    it('canonicals with pipe characters do not collide with each other', () => {
      // If flat string keys were used: "A|B|1m" and "A|B" + tf "1m" would both produce
      // "A|B|1m" — indistinguishable. Nested Maps eliminate the collision entirely.
      const PIPE_CANONICAL = 'A|B';
      const OTHER_CANONICAL = 'A';
      const PIPE_TF = 'B|1m'; // edge-case tf containing pipe

      useLiveTailStore.getState().recordSeen(PIPE_CANONICAL, TF, BUCKET, 1);
      useLiveTailStore.getState().recordSeen(OTHER_CANONICAL, PIPE_TF, BUCKET, 99);

      // They must not interfere with each other.
      expect(useLiveTailStore.getState().shouldAccept(PIPE_CANONICAL, TF, BUCKET, 1)).toBe(false);
      expect(useLiveTailStore.getState().shouldAccept(OTHER_CANONICAL, PIPE_TF, BUCKET, 99)).toBe(false);
      expect(useLiveTailStore.getState().shouldAccept(PIPE_CANONICAL, TF, BUCKET, 2)).toBe(true);
    });

    it('lockedBuckets pipe collision guard', () => {
      const PIPE_CANONICAL = 'X|Y';
      useLiveTailStore.getState().lockBucket(PIPE_CANONICAL, TF, BUCKET);
      // Must not lock ordinary canonical at same tf+bucket.
      expect(useLiveTailStore.getState().shouldAccept(CANONICAL, TF, BUCKET, 1)).toBe(true);
    });
  });
});
