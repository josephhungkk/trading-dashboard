import type { ComboPreviewRequest, CombosListResponse, ConfirmRequest, PreviewResponse } from './types';

const BASE = '/api/combos';

export async function previewCombo(payload: ComboPreviewRequest): Promise<PreviewResponse> {
  const r = await fetch(`${BASE}/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw await r.json();
  return r.json();
}

export async function confirmCombo(nonce: string, payload: ConfirmRequest): Promise<{ id: string }> {
  const r = await fetch(`${BASE}/confirm/${nonce}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Csrf-Nonce': nonce },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw await r.json();
  return r.json();
}

export async function cancelCombo(comboId: string, csrfNonce: string): Promise<void> {
  const r = await fetch(`${BASE}/${comboId}`, {
    method: 'DELETE',
    headers: { 'X-Csrf-Nonce': csrfNonce },
  });
  if (!r.ok && r.status !== 204) throw await r.json();
}

export async function listCombos(_accountId: string, status?: string): Promise<CombosListResponse> {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  const r = await fetch(`${BASE}?${params}`);
  if (!r.ok) throw await r.json();
  return r.json();
}
