import type { CFDInstrument } from './types';

const BASE = '/api/cfd';

export async function searchCFDs(q: string, limit = 20): Promise<CFDInstrument[]> {
  const resp = await fetch(
    `${BASE}/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    { credentials: 'include' },
  );
  if (!resp.ok) throw new Error(`searchCFDs failed: ${resp.status}`);
  return resp.json() as Promise<CFDInstrument[]>;
}

export async function fetchCFD(instrumentId: number): Promise<CFDInstrument> {
  const resp = await fetch(`${BASE}/${instrumentId}`, { credentials: 'include' });
  if (!resp.ok) throw new Error(`fetchCFD failed: ${resp.status}`);
  return resp.json() as Promise<CFDInstrument>;
}
