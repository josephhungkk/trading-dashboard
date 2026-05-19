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
  const q = params
    ? new URLSearchParams(
        Object.fromEntries(
          Object.entries(params)
            .filter(([, value]) => value !== undefined)
            .map(([key, value]) => [key, String(value)]),
        ),
      ).toString()
    : '';
  return json(
    await fetch(`${BASE}/${encodeURIComponent(botId)}/advisor-decisions${q ? `?${q}` : ''}`),
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
