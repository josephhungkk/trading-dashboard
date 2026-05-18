import type { BondAccruedInterest, BondInstrument } from './types';

const BASE = '/api/bonds';

export async function searchBonds(q: string, limit = 20): Promise<BondInstrument[]> {
  const resp = await fetch(
    `${BASE}/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    { credentials: 'include' },
  );
  if (!resp.ok) throw new Error(`searchBonds failed: ${resp.status}`);
  return resp.json() as Promise<BondInstrument[]>;
}

export async function fetchBond(instrumentId: number): Promise<BondInstrument> {
  const resp = await fetch(`${BASE}/${instrumentId}`, { credentials: 'include' });
  if (!resp.ok) throw new Error(`fetchBond failed: ${resp.status}`);
  return resp.json() as Promise<BondInstrument>;
}

export async function fetchAccruedInterest(
  instrumentId: number,
  accountId: string,
): Promise<BondAccruedInterest | null> {
  const resp = await fetch(
    `${BASE}/${instrumentId}/accrued?account_id=${encodeURIComponent(accountId)}`,
    { credentials: 'include' },
  );
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`fetchAccruedInterest failed: ${resp.status}`);
  return resp.json() as Promise<BondAccruedInterest>;
}
