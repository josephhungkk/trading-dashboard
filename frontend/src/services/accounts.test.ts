import { describe, it, expect } from 'vitest';
import { MockAccountsService } from './accounts';

describe('MockAccountsService', () => {
  const svc = new MockAccountsService();

  it('list(live) returns only live-mode accounts', async () => {
    const accounts = await svc.list('live');
    expect(accounts.length).toBeGreaterThan(0);
    expect(accounts.every(a => a.mode === 'live')).toBe(true);
  });

  it('list(paper) returns only paper-mode accounts', async () => {
    const accounts = await svc.list('paper');
    expect(accounts.length).toBeGreaterThan(0);
    expect(accounts.every(a => a.mode === 'paper')).toBe(true);
  });

  it('live and paper lists are disjoint', async () => {
    const live = await svc.list('live');
    const paper = await svc.list('paper');
    const liveIds = new Set(live.map(a => a.id));
    expect(paper.every(a => !liveIds.has(a.id))).toBe(true);
  });

  it('subscribe returns an unsubscribe function', () => {
    const unsub = svc.subscribe('live', () => {
      /* noop */
    });
    expect(typeof unsub).toBe('function');
    expect(() => unsub()).not.toThrow();
  });
});
