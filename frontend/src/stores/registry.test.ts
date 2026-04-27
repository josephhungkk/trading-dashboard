import { describe, it, expect, beforeEach } from 'vitest';
import { getScopedStores, getBothScopes } from './registry';
import { getServices, resetServices } from '@/services/registry';
import { ACCOUNTS } from '@/services/fixtures';
import type { Account, Mode } from '@/services/types';

async function fetchFixtureAccounts(mode: Mode): Promise<Account[]> {
  return ACCOUNTS.filter(account => account.mode === mode);
}

describe('stores registry', () => {
  beforeEach(() => {
    resetServices();
    const { live, paper } = getBothScopes();
    live.suspend();
    paper.suspend();
  });

  it('live and paper are distinct store instances', () => {
    const { live, paper } = getBothScopes();
    expect(live).not.toBe(paper);
    expect(live.useAccounts).not.toBe(paper.useAccounts);
    expect(live.usePositions).not.toBe(paper.usePositions);
  });

  it('hydrating live does not populate paper', async () => {
    const { live, paper } = getBothScopes();
    const svc = getServices();
    await live.hydrate(svc, fetchFixtureAccounts);
    expect(live.useAccounts.getState().accounts.length).toBeGreaterThan(0);
    expect(paper.useAccounts.getState().accounts.length).toBe(0);
  });

  it('suspend clears scope state', async () => {
    const { live } = getBothScopes();
    const svc = getServices();
    await live.hydrate(svc, fetchFixtureAccounts);
    expect(live.useAccounts.getState().accounts.length).toBeGreaterThan(0);
    live.suspend();
    expect(live.useAccounts.getState().accounts.length).toBe(0);
    expect(live.usePositions.getState().positions.length).toBe(0);
  });

  it('getScopedStores returns matching singleton', () => {
    const { live, paper } = getBothScopes();
    expect(getScopedStores('live')).toBe(live);
    expect(getScopedStores('paper')).toBe(paper);
  });
});
