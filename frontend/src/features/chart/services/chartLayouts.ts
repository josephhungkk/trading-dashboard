export interface ChartLayout {
  payload: Record<string, unknown>;
  schema_version: number;
  updated_at: string; // ISO8601, also serves as etag
}

/**
 * Resolve a canonical_id string (e.g. "stock:AAPL:US") to the numeric
 * instrument_id required by the chart_layouts CRUD endpoints (Task 37).
 *
 * Returns null if the canonical_id is not found in the instruments table (404).
 * Throws on other non-ok responses.
 */
export async function resolveInstrumentId(canonicalId: string): Promise<number | null> {
  const url = `/api/chart/layouts/resolve?canonical_id=${encodeURIComponent(canonicalId)}`;
  const res = await fetch(url, { credentials: 'same-origin' });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`instrument resolve failed: ${res.status}`);
  const body = (await res.json()) as { instrument_id: number };
  return body.instrument_id;
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
