import type {
  AdvisorConfig,
  AdvisorConfigResponse,
  AdvisorDecision,
  AdvisorDecisionsPage,
} from './types';

const BASE = '/api/bots';

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export async function getAdvisorConfig(botId: string): Promise<AdvisorConfigResponse> {
  return json(await fetch(`${BASE}/${encodeURIComponent(botId)}/advisor-config`));
}

export async function updateAdvisorConfig(
  botId: string,
  config: AdvisorConfig,
  csrfNonce: string,
): Promise<AdvisorConfigResponse> {
  return json(
    await fetch(`${BASE}/${encodeURIComponent(botId)}/advisor-config`, {
      method: 'PUT',
      headers: {
        'X-Confirm-Nonce': csrfNonce,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(config),
    }),
  );
}

export async function getAdvisorDecisions(
  botId: string,
  params?: { limit?: number; before?: string },
): Promise<AdvisorDecisionsPage> {
  const q = new URLSearchParams();
  if (params?.limit !== undefined) q.set('limit', String(params.limit));
  if (params?.before) q.set('before', params.before);
  const qs = q.toString();
  return json(
    await fetch(`${BASE}/${encodeURIComponent(botId)}/advisor-decisions${qs ? `?${qs}` : ''}`),
  );
}

export async function getAdvisorDecision(
  botId: string,
  decisionId: number,
): Promise<AdvisorDecision> {
  return json(
    await fetch(
      `${BASE}/${encodeURIComponent(botId)}/advisor-decisions/${encodeURIComponent(decisionId)}`,
    ),
  );
}

export async function getAdvisorFeed(filters?: {
  bot_id?: string;
  verdict?: string;
}): Promise<AdvisorDecision[]> {
  const q = new URLSearchParams();
  if (filters?.bot_id) q.set('bot_id', filters.bot_id);
  if (filters?.verdict) q.set('verdict', filters.verdict);
  const qs = q.toString();
  return json(await fetch(`${BASE}/advisor-feed${qs ? `?${qs}` : ''}`));
}
