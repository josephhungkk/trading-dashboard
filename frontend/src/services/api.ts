const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? '';

export interface HealthResponse {
  status: string;
  env: string;
  db: string;
}

export async function getHealth(): Promise<HealthResponse> {
  const r = await fetch(`${BASE}/health`);
  if (!r.ok) throw new Error(`health ${r.status}`);
  return (await r.json()) as HealthResponse;
}
