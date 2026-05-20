import type {
  BotHealthSnapshot,
  BotHealthSnapshotHistory,
  CorrelationMatrix,
  ExposureLimit,
  GeneratedStrategy,
} from './types';

const BASE = '/api/orchestrator';

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export async function getDigestLatest(): Promise<BotHealthSnapshot[]> {
  return json(await fetch(`${BASE}/digest/latest`));
}

export async function getDigestHistory(
  botId: string,
): Promise<BotHealthSnapshotHistory[]> {
  return json(await fetch(`${BASE}/digest/history/${botId}`));
}

export async function getCorrelation(
  accountId: string,
): Promise<CorrelationMatrix> {
  return json(await fetch(`${BASE}/correlation?account_id=${accountId}`));
}

export async function getExposureLimits(): Promise<ExposureLimit[]> {
  return json(await fetch(`${BASE}/exposure-limits`));
}

export async function getGeneratedStrategies(): Promise<GeneratedStrategy[]> {
  return json(await fetch('/api/strategy-gen'));
}

export async function approveStrategy(id: string): Promise<void> {
  await json(
    await fetch(`/api/strategy-gen/${id}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    }),
  );
}

export async function rejectStrategy(id: string): Promise<void> {
  await json(
    await fetch(`/api/strategy-gen/${id}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    }),
  );
}
