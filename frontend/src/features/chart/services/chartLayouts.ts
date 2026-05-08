export interface ChartLayout {
  payload: Record<string, unknown>;
  schema_version: number;
  updated_at: string; // ISO8601, also serves as etag
}

/** MED-4: typed error so callers can `instanceof`-check instead of matching `.message`. */
export class EtagMismatchError extends Error {
  constructor() {
    super('etag_mismatch');
    this.name = 'EtagMismatchError';
  }
}

/**
 * HIGH-3: optional `signal` parameter threads AbortSignal into the GET request,
 * used by the conflict-path recovery in layoutSync.ts.
 */
export async function getChartLayout(
  instrumentId: number,
  signal?: AbortSignal,
): Promise<ChartLayout | null> {
  const init: RequestInit = { credentials: 'same-origin' };
  if (signal) init.signal = signal;
  const res = await fetch(`/api/chart/layouts/${instrumentId}`, init);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`layout fetch failed: ${res.status}`);
  return res.json() as Promise<ChartLayout>;
}

export async function putChartLayout(
  instrumentId: number,
  layout: Omit<ChartLayout, 'updated_at'>,
  ifMatchEtag: string,
  signal?: AbortSignal,
): Promise<ChartLayout> {
  // MED-6: assert no control characters rather than silently stripping them.
  // The etag value is a server-controlled ISO timestamp (ASCII printable); if it
  // somehow contains control chars something has gone wrong upstream.
  if (ifMatchEtag !== '' && /[^\x20-\x7E]/.test(ifMatchEtag)) {
    throw new Error('invalid etag: contains control characters');
  }

  // HIGH-7: Empty etag wraps to `If-Match: ""` which the backend INSERT path tolerates;
  // the strict `_etag()` compare only fires when an existing row is found.
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'If-Match': `"${ifMatchEtag}"`,
  };

  // HIGH-4: fetch accepts `signal?: AbortSignal` (undefined is correct; null is type-incorrect).
  const init: RequestInit = {
    method: 'PUT',
    credentials: 'same-origin',
    headers,
    body: JSON.stringify(layout),
  };
  if (signal) init.signal = signal;

  const res = await fetch(`/api/chart/layouts/${instrumentId}`, init);
  // MED-4: throw typed error so callers can `instanceof EtagMismatchError`.
  if (res.status === 412) throw new EtagMismatchError();
  if (!res.ok) throw new Error(`layout put failed: ${res.status}`);
  return res.json() as Promise<ChartLayout>;
}
