import type { BacktestJob, BacktestJobDetail, BacktestSubmitConfig } from './types';

const base = (botId: string) => `/api/bots/${botId}/backtests`;

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

async function checkOk(res: Response): Promise<void> {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
}

export async function submitBacktest(
  botId: string,
  config: BacktestSubmitConfig,
): Promise<{ job_id: string }> {
  return json(
    await fetch(base(botId), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    }),
  );
}

export async function listBacktests(
  botId: string,
  cursor?: string,
): Promise<{ items: BacktestJob[]; next_cursor: string | null }> {
  const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
  return json(await fetch(`${base(botId)}${q}`));
}

export async function getBacktest(botId: string, jobId: string): Promise<BacktestJobDetail> {
  return json(await fetch(`${base(botId)}/${jobId}`));
}

export async function cancelBacktest(botId: string, jobId: string): Promise<void> {
  await checkOk(await fetch(`${base(botId)}/${jobId}`, { method: 'DELETE' }));
}

export async function uploadBars(
  botId: string,
  file: File,
  canonicalId: string,
  timeframe: string,
): Promise<{ upload_id: string; canonical_id: string; bar_count: number }> {
  const fd = new FormData();
  fd.append('file', file);
  return json(
    await fetch(
      `${base(botId)}/upload-bars?canonical_id=${encodeURIComponent(canonicalId)}&timeframe=${timeframe}`,
      { method: 'POST', body: fd },
    ),
  );
}
