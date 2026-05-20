import type {
  ApproveStrategyRequest,
  ApproveStrategyResponse,
  GenerateStrategyRequest,
  GenerateStrategyResponse,
  GeneratedStrategy,
  GeneratedStrategyDetail,
  RejectStrategyResponse,
} from './types';

const BASE = '/api/strategy-gen';

export async function listStrategies(): Promise<GeneratedStrategy[]> {
  const resp = await fetch(BASE);
  if (!resp.ok) throw new Error();
  return resp.json() as Promise<GeneratedStrategy[]>;
}

export async function getStrategy(id: number): Promise<GeneratedStrategyDetail> {
  const resp = await fetch(`${BASE}/${id}`);
  if (!resp.ok) throw new Error();
  return resp.json() as Promise<GeneratedStrategyDetail>;
}

export async function generateStrategy(
  body: GenerateStrategyRequest,
): Promise<GenerateStrategyResponse> {
  const resp = await fetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error();
  return resp.json() as Promise<GenerateStrategyResponse>;
}

export async function approveStrategy(
  id: number,
  body: ApproveStrategyRequest,
  nonce: string,
): Promise<ApproveStrategyResponse> {
  const resp = await fetch(`${BASE}/${id}/approve`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Confirm-Nonce': nonce,
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error();
  return resp.json() as Promise<ApproveStrategyResponse>;
}

export async function rejectStrategy(
  id: number,
  nonce: string,
): Promise<RejectStrategyResponse> {
  const resp = await fetch(`${BASE}/${id}/reject`, {
    method: 'POST',
    headers: { 'X-Confirm-Nonce': nonce },
  });
  if (!resp.ok) throw new Error();
  return resp.json() as Promise<RejectStrategyResponse>;
}
