import type { ShadowComparisonReport } from './types';

const BASE = (botId: string) => `/api/bots/${botId}`;

async function checkOk(res: Response): Promise<void> {
  if (!res.ok) {
    throw new Error(await res.text());
  }
}

async function json<T>(res: Response): Promise<T> {
  await checkOk(res);
  return (await res.json()) as T;
}

export async function createShadow(
  botId: string,
  overrideParams: Record<string, unknown>,
  comparisonWindowDays: number,
  csrfNonce: string,
): Promise<{ shadow_bot_id: string }> {
  const res = await fetch(`${BASE(botId)}/shadows`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-Confirm-Nonce': csrfNonce },
    body: JSON.stringify({
      override_params: overrideParams,
      comparison_window_days: comparisonWindowDays,
    }),
  });
  return json<{ shadow_bot_id: string }>(res);
}

export async function getShadowComparison(botId: string): Promise<ShadowComparisonReport> {
  const res = await fetch(`${BASE(botId)}/shadows/comparison`, { credentials: 'include' });
  if (res.status === 404) {
    return { live_bot_id: botId, shadows: [], generated_at: new Date(0).toISOString() };
  }
  return json<ShadowComparisonReport>(res);
}

export async function promoteShadow(
  botId: string,
  shadowId: string,
  csrfNonce: string,
): Promise<void> {
  const res = await fetch(`${BASE(botId)}/shadows/${shadowId}/promote`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-Confirm-Nonce': csrfNonce },
  });
  await checkOk(res);
}

export async function listShadowPromotions(
  botId: string,
): Promise<{ items: Record<string, unknown>[] }> {
  const res = await fetch(`${BASE(botId)}/shadow-promotions`, { credentials: 'include' });
  return json<{ items: Record<string, unknown>[] }>(res);
}
