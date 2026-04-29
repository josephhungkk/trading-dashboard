import type { components } from '@/services/api-generated';

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

type FillListResponse = components['schemas']['FillListResponse'];

export interface FetchFillsParams {
  account_id: string;
  from: string;
  to: string;
  limit?: number;
  cursor?: string;
}

export async function fetchFills(params: FetchFillsParams): Promise<FillListResponse> {
  const query = new URLSearchParams();
  query.set('account_id', params.account_id);
  query.set('from', params.from);
  query.set('to', params.to);
  if (params.limit !== undefined) query.set('limit', String(params.limit));
  if (params.cursor !== undefined) query.set('cursor', params.cursor);
  const r = await fetch(`${BASE}/api/fills?${query.toString()}`);
  if (!r.ok) throw new Error(`fills ${r.status}`);
  return (await r.json()) as FillListResponse;
}

type OrderResponse = components['schemas']['OrderResponse'];

export async function fetchOrder(id: string): Promise<OrderResponse> {
  const r = await fetch(`${BASE}/api/orders/${encodeURIComponent(id)}`);
  if (!r.ok) throw new Error(`order ${r.status}`);
  return (await r.json()) as OrderResponse;
}
