import { adminFetch, mintCsrfNonce } from '@/services/admin/api';

import type {
  AlertRule,
  CreateAlertRequest,
  CreateAlertResponse,
  RecentFire,
} from './types';

export async function postAlert(req: CreateAlertRequest): Promise<CreateAlertResponse> {
  const nonce = await mintCsrfNonce();
  return adminFetch<CreateAlertResponse>('/api/alerts', {
    method: 'POST',
    body: JSON.stringify(req),
    headers: { 'X-CSRF-Nonce': nonce },
  });
}

export async function getAlert(id: number): Promise<AlertRule> {
  return adminFetch<AlertRule>(`/api/alerts/${id}`, { method: 'GET' });
}

export async function listAlerts(): Promise<{ alerts: AlertRule[] }> {
  return adminFetch<{ alerts: AlertRule[] }>('/api/alerts', { method: 'GET' });
}

export async function putPredicate(
  id: number,
  predicate_json: Record<string, unknown>,
): Promise<AlertRule> {
  const nonce = await mintCsrfNonce();
  return adminFetch<AlertRule>(`/api/alerts/${id}`, {
    method: 'PUT',
    body: JSON.stringify({ predicate_json }),
    headers: { 'X-CSRF-Nonce': nonce },
  });
}

export async function deleteAlert(id: number): Promise<void> {
  const nonce = await mintCsrfNonce();
  await adminFetch<undefined>(`/api/alerts/${id}`, {
    method: 'DELETE',
    headers: { 'X-CSRF-Nonce': nonce },
  });
}

export async function confirmAlert(id: number): Promise<AlertRule> {
  const nonce = await mintCsrfNonce();
  return adminFetch<AlertRule>(`/api/alerts/${id}/confirm`, {
    method: 'POST',
    headers: { 'X-CSRF-Nonce': nonce },
  });
}

export async function getRecentFires(
  since: string | null,
  limit = 50,
): Promise<{ fires: RecentFire[] }> {
  const q = new URLSearchParams({ limit: String(limit) });
  if (since) q.set('since', since);
  return adminFetch<{ fires: RecentFire[] }>(
    `/api/alerts/recent-fires?${q.toString()}`,
    { method: 'GET' },
  );
}
