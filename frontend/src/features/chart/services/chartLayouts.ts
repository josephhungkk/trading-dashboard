export interface ChartLayout {
  payload: Record<string, unknown>;
  schema_version: number;
  updated_at: string; // ISO8601, also serves as etag
}

export async function getChartLayout(instrumentId: number): Promise<ChartLayout | null> {
  const res = await fetch(`/api/chart/layouts/${instrumentId}`, { credentials: 'same-origin' });
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
  const res = await fetch(`/api/chart/layouts/${instrumentId}`, {
    method: 'PUT',
    credentials: 'same-origin',
    signal: signal ?? null,
    headers: {
      'Content-Type': 'application/json',
      // HIGH-7: strip control characters before injecting into header value.
      'If-Match': `"${ifMatchEtag.replace(/[^\x20-\x7E]/g, '')}"`,
    },
    body: JSON.stringify(layout),
  });
  if (res.status === 412) throw new Error('etag_mismatch');
  if (!res.ok) throw new Error(`layout put failed: ${res.status}`);
  return res.json() as Promise<ChartLayout>;
}
