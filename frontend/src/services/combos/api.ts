import type { ComboPreviewRequest, CombosListResponse, ConfirmRequest, PreviewResponse } from './types';

const BASE = '/api/combos';

const CREDS: RequestInit = { credentials: 'include' };

async function _throw(r: Response): Promise<never> {
  let body: unknown;
  try {
    body = await r.json();
  } catch {
    body = { detail: r.statusText };
  }
  throw body;
}

export async function previewCombo(payload: ComboPreviewRequest): Promise<PreviewResponse> {
  const r = await fetch(`${BASE}/preview`, {
    ...CREDS,
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!r.ok) return _throw(r);
  return r.json();
}

export async function confirmCombo(nonce: string, payload: ConfirmRequest): Promise<{ id: string }> {
  const r = await fetch(`${BASE}/confirm/${nonce}`, {
    ...CREDS,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Csrf-Nonce': nonce },
    body: JSON.stringify(payload),
  });
  if (!r.ok) return _throw(r);
  return r.json();
}

export async function cancelCombo(comboId: string, csrfNonce: string): Promise<void> {
  const r = await fetch(`${BASE}/${comboId}`, {
    ...CREDS,
    method: 'DELETE',
    headers: { 'X-Csrf-Nonce': csrfNonce },
  });
  if (!r.ok && r.status !== 204) return _throw(r);
}

export async function listCombos(accountId: string, status?: string): Promise<CombosListResponse> {
  const params = new URLSearchParams({ account_id: accountId });
  if (status) params.set('status', status);
  const r = await fetch(`${BASE}?${params}`, CREDS);
  if (!r.ok) return _throw(r);
  return r.json();
}
