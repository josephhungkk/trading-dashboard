import { describe, it, expect } from 'vitest';
import { ACCOUNTS, POSITIONS, ORDERS, WATCHLISTS, SYMBOLS, STRESS_SYMBOLS, BROKERS } from './index';

describe('fixtures', () => {
  it('6 accounts — 2 per broker × 3 brokers', () => {
    expect(ACCOUNTS).toHaveLength(6);
    for (const b of BROKERS) {
      const mine = ACCOUNTS.filter(a => a.broker === b.id);
      expect(mine).toHaveLength(2);
      expect(mine.map(a => a.mode).sort()).toEqual(['live', 'paper']);
    }
  });
  it('every position refs known account + symbol', () => {
    const aIds = new Set(ACCOUNTS.map(a => a.id));
    const sIds = new Set([...SYMBOLS, ...STRESS_SYMBOLS].map(s => s.symbol));
    for (const p of POSITIONS) {
      expect(aIds).toContain(p.accountId);
      expect(sIds).toContain(p.symbol);
    }
  });
  it('orders span all statuses', () => {
    const set = new Set(ORDERS.map(o => o.status));
    for (const s of ['open','filled','partial','cancelled','rejected'] as const) expect(set).toContain(s);
  });
  it('watchlists span stock/forex/crypto + stress-500', () => {
    const classes = new Set(WATCHLISTS.map(w => w.assetClass));
    expect(classes).toContain('stock');
    expect(classes).toContain('forex');
    expect(classes).toContain('crypto');
    const stress = WATCHLISTS.find(w => w.id === 'stress-500');
    expect(stress).toBeDefined();
    expect(stress?.symbolIds).toHaveLength(500);
  });
});
