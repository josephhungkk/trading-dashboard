/**
 * Phase 10b.2 — fetch wrappers for /api/portfolio/{rollup,curve,drill}.
 * Mirrors services/sizing/api.ts (same fetchJson shape; no CSRF nonce
 * because these are read-only GET endpoints).
 */

import type {
  BaseCurrency,
  CurveWindow,
  RollupCurve,
  RollupDrill,
  RollupLive,
} from '@/services/portfolio/types';

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

export interface PortfolioApiError extends Error {
  status: number;
  payload: unknown;
}

export function isPortfolioApiError(err: unknown): err is PortfolioApiError {
  return (
    err instanceof Error
    && 'status' in err
    && typeof (err as { status: unknown }).status === 'number'
  );
}

function extractDetail(payload: unknown): string | null {
  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
    return null;
  }
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const errField = (detail as { error?: unknown }).error;
    if (typeof errField === 'string') return errField;
  }
  return null;
}

async function buildError(response: Response): Promise<PortfolioApiError> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  const detail = extractDetail(payload);
  const message = detail ?? `portfolio api ${response.status}`;
  const err = new Error(message) as PortfolioApiError;
  err.status = response.status;
  err.payload = payload;
  return err;
}

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

// All query-string params encoded for defence-in-depth; BaseCurrency and
// CurveWindow are closed string unions today so injection is type-impossible,
// but the encoding survives any future type widening (reviewer MED).
export const fetchRollupLive = (base: BaseCurrency): Promise<RollupLive> =>
  fetchJson<RollupLive>(
    `/api/portfolio/rollup?base=${encodeURIComponent(base)}`,
  );

export const fetchRollupCurve = (
  base: BaseCurrency,
  window: CurveWindow,
): Promise<RollupCurve> =>
  fetchJson<RollupCurve>(
    `/api/portfolio/rollup/curve?base=${encodeURIComponent(base)}&window=${encodeURIComponent(window)}`,
  );

export const fetchRollupDrill = (
  assetClass: string,
  base: BaseCurrency,
): Promise<RollupDrill> =>
  fetchJson<RollupDrill>(
    `/api/portfolio/rollup/drill?asset_class=${encodeURIComponent(assetClass)}&base=${encodeURIComponent(base)}`,
  );
