import { mintCsrfNonce } from '@/services/admin/api';

import type { ParamSuggestion } from './types';

const BASE = (botId: string) => `/api/bots/${botId}/param-suggestions`;

async function checkOk(res: Response): Promise<void> {
  if (!res.ok) {
    throw new Error(await res.text());
  }
}

async function json<T>(res: Response): Promise<T> {
  await checkOk(res);
  return (await res.json()) as T;
}

export { mintCsrfNonce };

export async function triggerParamSuggestion(
  botId: string,
  csrfNonce: string,
): Promise<{ suggestion_id: string }> {
  const res = await fetch(BASE(botId), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-Confirm-Nonce': csrfNonce },
  });
  return json<{ suggestion_id: string }>(res);
}

export async function listParamSuggestions(
  botId: string,
): Promise<{ items: ParamSuggestion[] }> {
  const res = await fetch(BASE(botId), { credentials: 'include' });
  return json<{ items: ParamSuggestion[] }>(res);
}

export async function approveParamSuggestion(
  botId: string,
  suggestionId: string,
  candidateIndex: number,
  csrfNonce: string,
): Promise<void> {
  const res = await fetch(`${BASE(botId)}/${suggestionId}/approve`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-Confirm-Nonce': csrfNonce },
    body: JSON.stringify({ candidate_index: candidateIndex }),
  });
  await checkOk(res);
}

export async function rejectParamSuggestion(
  botId: string,
  suggestionId: string,
  csrfNonce: string,
): Promise<void> {
  const res = await fetch(`${BASE(botId)}/${suggestionId}/reject`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-Confirm-Nonce': csrfNonce },
  });
  await checkOk(res);
}
