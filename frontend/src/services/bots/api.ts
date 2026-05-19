import type { Bot, BotCreate, BotOrder, BotRun, RiskCaps, StrategyFile } from './types';

const BASE = '/api/bots';

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

async function checkOk(res: Response): Promise<void> {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
}

export async function listBots(params?: {
  status?: string;
  mode?: string;
  cursor?: string;
}): Promise<{ items: Bot[]; next_cursor: string | null }> {
  const q = params
    ? new URLSearchParams(
        Object.fromEntries(
          Object.entries(params).filter(([, v]) => v !== undefined),
        ) as Record<string, string>,
      ).toString()
    : '';
  return json(await fetch(`${BASE}${q ? `?${q}` : ''}`));
}

export async function getBot(id: string): Promise<Bot> {
  return json(await fetch(`${BASE}/${id}`));
}

export async function createBot(body: BotCreate): Promise<Bot> {
  return json(
    await fetch(BASE, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  );
}

export async function updateBot(
  id: string,
  body: Partial<Pick<BotCreate, 'name' | 'params_json' | 'bar_timeframe'>>,
): Promise<Bot> {
  return json(
    await fetch(`${BASE}/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  );
}

export async function deleteBot(id: string): Promise<void> {
  await checkOk(await fetch(`${BASE}/${id}`, { method: 'DELETE' }));
}

export async function startBot(id: string): Promise<{ status: string }> {
  return json(await fetch(`${BASE}/${id}/start`, { method: 'POST' }));
}

export async function stopBot(id: string): Promise<{ status: string }> {
  return json(await fetch(`${BASE}/${id}/stop`, { method: 'POST' }));
}

export async function pauseBot(id: string): Promise<void> {
  await checkOk(await fetch(`${BASE}/${id}/pause`, { method: 'POST' }));
}

export async function resumeBot(id: string): Promise<void> {
  await checkOk(await fetch(`${BASE}/${id}/resume`, { method: 'POST' }));
}

export async function deployBot(id: string): Promise<{ version: number }> {
  return json(await fetch(`${BASE}/${id}/deploy`, { method: 'POST' }));
}

export async function upsertRiskCaps(id: string, caps: RiskCaps): Promise<void> {
  await checkOk(
    await fetch(`${BASE}/${id}/risk-caps`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(caps),
    }),
  );
}

export async function listRuns(
  id: string,
  cursor?: string,
): Promise<{ items: BotRun[]; next_cursor: string | null }> {
  const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
  return json(await fetch(`${BASE}/${id}/runs${q}`));
}

export async function listBotOrders(
  id: string,
  cursor?: string,
): Promise<{ items: BotOrder[]; next_cursor: string | null }> {
  const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
  return json(await fetch(`${BASE}/${id}/orders${q}`));
}

export async function listStrategies(): Promise<StrategyFile[]> {
  return json(await fetch(`${BASE}/strategies`));
}
