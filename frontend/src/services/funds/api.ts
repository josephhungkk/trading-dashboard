import type { FundInstrument, FundNavSnapshot } from './types';

const BASE = '/api/funds';

export async function searchFunds(q: string, limit = 20): Promise<FundInstrument[]> {
  const resp = await fetch(
    `${BASE}/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    { credentials: 'include' },
  );
  if (!resp.ok) throw new Error(`searchFunds failed: ${resp.status}`);
  return resp.json() as Promise<FundInstrument[]>;
}

export async function fetchFund(instrumentId: number): Promise<FundInstrument> {
  const resp = await fetch(`${BASE}/${instrumentId}`, { credentials: 'include' });
  if (!resp.ok) throw new Error(`fetchFund failed: ${resp.status}`);
  return resp.json() as Promise<FundInstrument>;
}

export async function fetchNavSnapshot(instrumentId: number): Promise<FundNavSnapshot | null> {
  const resp = await fetch(`${BASE}/${instrumentId}/nav`, { credentials: 'include' });
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`fetchNavSnapshot failed: ${resp.status}`);
  return resp.json() as Promise<FundNavSnapshot>;
}
