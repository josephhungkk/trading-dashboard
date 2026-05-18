// frontend/src/services/algo/api.ts
import type { AlgoCapabilitiesResponse, AlgoSchemasResponse } from './types';

async function fetchJson<T>(path: string): Promise<T> {
  const resp = await fetch(path, { credentials: 'include' });
  if (!resp.ok) throw new Error(`algo api ${resp.status}: ${path}`);
  return resp.json() as Promise<T>;
}

export async function getAlgoCapabilities(
  brokerId: string,
  assetClass: string,
): Promise<AlgoCapabilitiesResponse> {
  return fetchJson<AlgoCapabilitiesResponse>(
    `/api/algo/capabilities/${encodeURIComponent(brokerId)}/${encodeURIComponent(assetClass)}`,
  );
}

export async function getAlgoSchemas(): Promise<AlgoSchemasResponse> {
  return fetchJson<AlgoSchemasResponse>('/api/algo/schemas');
}
