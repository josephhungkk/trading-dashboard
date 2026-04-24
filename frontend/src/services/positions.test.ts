import { describe, it, expect } from 'vitest';
import { MockPositionsService } from './positions';
import { ACCOUNTS } from './fixtures';

describe('MockPositionsService', () => {
  const svc = new MockPositionsService();

  it('list(live) returns only positions for live-mode accounts', async () => {
    const positions = await svc.list('live');
    const liveAcctIds = new Set(ACCOUNTS.filter(a => a.mode === 'live').map(a => a.id));
    expect(positions.length).toBeGreaterThan(0);
    expect(positions.every(p => liveAcctIds.has(p.accountId))).toBe(true);
  });

  it('list(paper) returns only positions for paper-mode accounts', async () => {
    const positions = await svc.list('paper');
    const paperAcctIds = new Set(ACCOUNTS.filter(a => a.mode === 'paper').map(a => a.id));
    expect(positions.length).toBeGreaterThan(0);
    expect(positions.every(p => paperAcctIds.has(p.accountId))).toBe(true);
  });

  it('subscribe returns an unsubscribe function', () => {
    const unsub = svc.subscribe('live', () => {
      /* noop */
    });
    expect(typeof unsub).toBe('function');
    expect(() => unsub()).not.toThrow();
  });
});
