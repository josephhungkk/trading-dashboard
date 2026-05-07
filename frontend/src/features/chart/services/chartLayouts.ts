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
): Promise<ChartLayout> {
  const res = await fetch(`/api/chart/layouts/${instrumentId}`, {
    method: 'PUT',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'If-Match': `"${ifMatchEtag}"`,
    },
    body: JSON.stringify(layout),
  });
  if (res.status === 412) throw new Error('etag_mismatch');
  if (!res.ok) throw new Error(`layout put failed: ${res.status}`);
  return res.json() as Promise<ChartLayout>;
}
