import type {
  FutureContractMonth,
  FutureRollRule,
  FutureRollRuleRequest,
  FutureSettlementEvent,
  RollPreviewResponse,
} from './types';

const BASE = '/api/futures';

export async function fetchContracts(
  rootSymbol: string,
  broker = 'default',
): Promise<FutureContractMonth[]> {
  const resp = await fetch(
    `${BASE}/contracts/${encodeURIComponent(rootSymbol)}?broker=${encodeURIComponent(broker)}`,
    { credentials: 'include' },
  );
  if (!resp.ok) throw new Error(`Failed to fetch contracts: ${resp.status}`);
  return resp.json() as Promise<FutureContractMonth[]>;
}

export async function fetchRollRules(): Promise<FutureRollRule[]> {
  const resp = await fetch(`${BASE}/roll-rules`, { credentials: 'include' });
  if (!resp.ok) throw new Error(`Failed to fetch roll rules: ${resp.status}`);
  return resp.json() as Promise<FutureRollRule[]>;
}

export async function createRollRule(body: FutureRollRuleRequest): Promise<FutureRollRule> {
  const resp = await fetch(`${BASE}/roll-rules`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`Failed to create roll rule: ${resp.status}`);
  return resp.json() as Promise<FutureRollRule>;
}

export async function deleteRollRule(id: string): Promise<void> {
  const resp = await fetch(`${BASE}/roll-rules/${id}`, {
    method: 'DELETE',
    credentials: 'include',
  });
  if (!resp.ok) throw new Error(`Failed to delete roll rule: ${resp.status}`);
}

export async function fetchSettlements(cursor?: string): Promise<{
  items: FutureSettlementEvent[];
  next_cursor: string | null;
}> {
  const url = cursor
    ? `${BASE}/settlements?cursor=${encodeURIComponent(cursor)}`
    : `${BASE}/settlements`;
  const resp = await fetch(url, { credentials: 'include' });
  if (!resp.ok) throw new Error(`Failed to fetch settlements: ${resp.status}`);
  return resp.json() as Promise<{ items: FutureSettlementEvent[]; next_cursor: string | null }>;
}

export async function fetchRollPreview(instrumentId: number, accountId: string): Promise<RollPreviewResponse> {
  const resp = await fetch(`${BASE}/roll/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ instrument_id: instrumentId, account_id: accountId }),
  });
  if (!resp.ok) throw new Error(`Failed to fetch roll preview: ${resp.status}`);
  return resp.json() as Promise<RollPreviewResponse>;
}

export async function confirmRoll(nonce: string, csrfToken: string): Promise<{ status: string }> {
  const resp = await fetch(`${BASE}/roll/confirm/${encodeURIComponent(nonce)}`, {
    method: 'POST',
    headers: { 'X-CSRF-Nonce': csrfToken, 'Content-Type': 'application/json' },
    credentials: 'include',
  });
  if (!resp.ok) throw new Error(`Failed to confirm roll: ${resp.status}`);
  return resp.json() as Promise<{ status: string }>;
}
