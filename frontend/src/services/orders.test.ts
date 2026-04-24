import { describe, it, expect } from 'vitest';
import { MockOrdersService } from './orders';
import { ACCOUNTS } from './fixtures';

describe('MockOrdersService', () => {
  const svc = new MockOrdersService();

  it('list(live) returns only orders for live-mode accounts', async () => {
    const orders = await svc.list('live');
    const liveAcctIds = new Set(ACCOUNTS.filter(a => a.mode === 'live').map(a => a.id));
    expect(orders.length).toBeGreaterThan(0);
    expect(orders.every(o => liveAcctIds.has(o.accountId))).toBe(true);
  });

  it('list(paper) returns only orders for paper-mode accounts', async () => {
    const orders = await svc.list('paper');
    const paperAcctIds = new Set(ACCOUNTS.filter(a => a.mode === 'paper').map(a => a.id));
    expect(orders.length).toBeGreaterThan(0);
    expect(orders.every(o => paperAcctIds.has(o.accountId))).toBe(true);
  });

  it('subscribe returns an unsubscribe function', () => {
    const unsub = svc.subscribe('live', () => {
      /* noop */
    });
    expect(typeof unsub).toBe('function');
    expect(() => unsub()).not.toThrow();
  });
});
