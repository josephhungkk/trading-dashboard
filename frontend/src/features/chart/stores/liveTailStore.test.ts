import { describe, it, expect, beforeEach } from 'vitest';
import { useLiveTailStore, FINAL_REVISION_VAL } from './liveTailStore';

function resetStore(): void {
  useLiveTailStore.setState({
    lastSeen: new Map(),
    lockedBuckets: new Set(),
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
});
