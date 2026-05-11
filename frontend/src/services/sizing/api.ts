/**
 * Phase 10b.1 — fetch wrappers for /api/risk/position-size and the per-
 * account sizing-defaults endpoints. Mirrors services/risk/api.ts (same
 * fetchJson + CSRF nonce shape).
 */

import type {
  SizingDefaults,
  SizingDefaultsUpdate,
  SizingRequest,
  SizingResult,
} from '@/services/sizing/types';

const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? '';

function isSameOriginBase(base: string): boolean {
  if (base === '') return true;
  try {
    const url = new URL(
      base,
      typeof window === 'undefined' ? 'http://test' : window.location.href,
    );
    if (typeof window === 'undefined') return true;
    return url.origin === window.location.origin;
  } catch {
    return false;
  }
}

const CREDENTIALS_POLICY: RequestCredentials = isSameOriginBase(BASE)
  ? 'include'
  : 'same-origin';

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: CREDENTIALS_POLICY,
    headers,
  });
  if (!response.ok) {
    throw await buildError(response);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export interface SizingApiError extends Error {
  status: number;
  payload: unknown;
}

export function isSizingApiError(err: unknown): err is SizingApiError {
  return (
    err instanceof Error
    && 'status' in err
    && typeof (err as { status: unknown }).status === 'number'
  );
}

function extractDetail(payload: unknown): string | null {
  if (payload === null || typeof payload !== 'object') return null;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    const errField = (detail as { error?: unknown }).error;
    if (typeof errField === 'string') return errField;
  }
  return null;
}

async function buildError(response: Response): Promise<SizingApiError> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  const detail = extractDetail(payload);
  const message = detail ?? `sizing api ${response.status}`;
  const err = new Error(message) as SizingApiError;
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

export function computePositionSize(req: SizingRequest): Promise<SizingResult> {
  return fetchJson<SizingResult>('/api/risk/position-size', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export function getSizingDefaults(accountId: string): Promise<SizingDefaults> {
  return fetchJson<SizingDefaults>(
    `/api/risk/sizing-defaults/${encodeURIComponent(accountId)}`,
  );
}

export async function setSizingDefaults(
  accountId: string,
  payload: SizingDefaultsUpdate,
): Promise<void> {
  const nonce = await mintConfirmNonce();
  await fetchJson<undefined>(
    `/api/admin/sizing-defaults/${encodeURIComponent(accountId)}`,
    {
      method: 'PUT',
      headers: { 'X-Confirm-Nonce': nonce },
      body: JSON.stringify(payload),
    },
  );
}
