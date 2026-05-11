/**
 * Phase 10a — fetch wrappers for /api/risk + /api/admin/risk-limits +
 * /api/admin/accounts/{id}/kill-switch.
 *
 * The admin write endpoints require a single-use CSRF nonce minted from
 * /api/admin/csrf/issue and sent in the X-Confirm-Nonce header. The
 * `withConfirmNonce` helper handles the mint+attach flow so each
 * mutation hook only needs to call one function.
 */

import type {
  AccountKillSwitchOut,
  AccountKillSwitchToggleRequest,
  RiskDecisionOut,
  RiskDecisionsFilter,
  RiskLimitCreate,
  RiskLimitOut,
  RiskLimitUpdate,
} from '@/services/risk/types';

const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? '';

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers,
  });
  if (!response.ok) {
    throw await buildError(response);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export interface RiskApiError extends Error {
  status: number;
  payload: unknown;
}

async function buildError(response: Response): Promise<RiskApiError> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  const err = new Error(`risk api ${response.status}`) as RiskApiError;
  err.status = response.status;
  err.payload = payload;
  return err;
}

async function mintConfirmNonce(): Promise<string> {
  const result = await fetchJson<{ nonce: string }>('/api/admin/csrf/issue', {
    method: 'POST',
  });
  return result.nonce;
}

async function withConfirmNonce<T>(
  call: (headers: Record<string, string>) => Promise<T>,
): Promise<T> {
  const nonce = await mintConfirmNonce();
  return call({ 'X-Confirm-Nonce': nonce });
}

/* ── reads ────────────────────────────────────────────────────────── */

export function listRiskLimits(): Promise<RiskLimitOut[]> {
  return fetchJson<RiskLimitOut[]>('/api/risk/limits');
}

export function listRiskDecisions(
  filter: RiskDecisionsFilter = {},
): Promise<RiskDecisionOut[]> {
  const params = new URLSearchParams();
  if (filter.account_id) params.set('account_id', filter.account_id);
  if (filter.verdict) params.set('verdict', filter.verdict);
  if (filter.limit !== undefined) params.set('limit', String(filter.limit));
  const qs = params.toString();
  const path = qs ? `/api/risk/decisions?${qs}` : '/api/risk/decisions';
  return fetchJson<RiskDecisionOut[]>(path);
}

export function getAccountKillSwitch(
  accountId: string,
): Promise<AccountKillSwitchOut> {
  return fetchJson<AccountKillSwitchOut>(
    `/api/admin/accounts/${encodeURIComponent(accountId)}/kill-switch`,
  );
}

/* ── writes (CSRF-gated) ─────────────────────────────────────────── */

export function createRiskLimit(body: RiskLimitCreate): Promise<RiskLimitOut> {
  return withConfirmNonce((headers) =>
    fetchJson<RiskLimitOut>('/api/admin/risk-limits', {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    }),
  );
}

export function updateRiskLimit(
  id: number,
  body: RiskLimitUpdate,
): Promise<RiskLimitOut> {
  return withConfirmNonce((headers) =>
    fetchJson<RiskLimitOut>(`/api/admin/risk-limits/${id}`, {
      method: 'PUT',
      headers,
      body: JSON.stringify(body),
    }),
  );
}

export function deleteRiskLimit(id: number): Promise<undefined> {
  return withConfirmNonce((headers) =>
    fetchJson<undefined>(`/api/admin/risk-limits/${id}`, {
      method: 'DELETE',
      headers,
    }),
  );
}

export function setAccountKillSwitch(
  accountId: string,
  body: AccountKillSwitchToggleRequest,
): Promise<AccountKillSwitchOut> {
  return withConfirmNonce((headers) =>
    fetchJson<AccountKillSwitchOut>(
      `/api/admin/accounts/${encodeURIComponent(accountId)}/kill-switch`,
      {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
      },
    ),
  );
}
