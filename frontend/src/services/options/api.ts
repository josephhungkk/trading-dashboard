export type { ExerciseCandidate, ExerciseElection } from '@/services/options/types';
import type { ExerciseCandidate, ExerciseElection, OptionChainData } from '@/services/options/types';

const BASE = '/api/options';

export async function fetchExpirations(symbol: string, currency = 'USD'): Promise<string[]> {
  const resp = await fetch(
    `${BASE}/expirations?symbol=${encodeURIComponent(symbol)}&currency=${currency}`,
  );
  if (!resp.ok) throw new Error(`Failed to fetch expirations: ${resp.status}`);
  const data = (await resp.json()) as { expiry_dates: string[] };
  return data.expiry_dates;
}

export async function fetchChain(
  symbol: string,
  expiry: string,
  strikes = 20,
  currency = 'USD',
): Promise<OptionChainData> {
  const resp = await fetch(
    `${BASE}/chain?symbol=${encodeURIComponent(symbol)}&expiry=${expiry}&strikes=${strikes}&currency=${currency}`,
  );
  if (!resp.ok) throw new Error(`Failed to fetch chain: ${resp.status}`);
  return resp.json() as Promise<OptionChainData>;
}

export async function fetchExercisePending(accountId: string): Promise<ExerciseCandidate[]> {
  const resp = await fetch(`${BASE}/exercise?account_id=${accountId}`);
  if (!resp.ok) throw new Error(`Failed to fetch exercise candidates: ${resp.status}`);
  return resp.json() as Promise<ExerciseCandidate[]>;
}

export async function fetchExerciseElections(): Promise<ExerciseElection[]> {
  const resp = await fetch(`${BASE}/events`);
  if (!resp.ok) throw new Error(`Failed to fetch exercise elections: ${resp.status}`);
  return resp.json() as Promise<ExerciseElection[]>;
}

export async function mintCsrfNonce(): Promise<string> {
  const resp = await fetch('/api/auth/csrf-nonce', { method: 'POST' });
  if (!resp.ok) throw new Error('Failed to mint CSRF nonce');
  const data = (await resp.json()) as { nonce: string };
  return data.nonce;
}

export async function postExerciseElection(
  body: {
    account_id: string;
    instrument_id: number;
    action: 'EXERCISE' | 'DO_NOT_EXERCISE' | 'LAPSE';
    qty: string;
    idempotency_key: string;
  },
  csrfNonce: string,
): Promise<{ id: string; status: string }> {
  const resp = await fetch(`${BASE}/exercise`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Confirm-Nonce': csrfNonce },
    body: JSON.stringify(body),
  });
  if (resp.status === 409) throw new Error('duplicate_election');
  if (resp.status === 429) throw new Error('rate_limit_exceeded');
  if (!resp.ok) throw new Error(`Exercise election failed: ${resp.status}`);
  return resp.json() as Promise<{ id: string; status: string }>;
}
