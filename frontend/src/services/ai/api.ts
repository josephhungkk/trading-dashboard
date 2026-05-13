/**
 * Phase 11a-D — fetch wrappers for /api/ai endpoints.
 * Mirrors services/portfolio/api.ts; websocket traffic is handled by hooks.
 */

import type {
  CompletionRequest,
  CompletionResult,
  JobStatusResponse,
  JobSubmitResponse,
} from '@/services/ai/types';

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

export interface AiApiError extends Error {
  status: number;
  payload: unknown;
}

export function isAiApiError(err: unknown): err is AiApiError {
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

async function buildError(response: Response): Promise<AiApiError> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  const detail = extractDetail(payload);
  const message = detail ?? `ai api ${response.status}`;
  const err = new Error(message) as AiApiError;
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

export const postComplete = (
  req: CompletionRequest,
): Promise<CompletionResult> =>
  fetchJson<CompletionResult>('/api/ai/complete', {
    method: 'POST',
    body: JSON.stringify(req),
  });

export const postJob = (
  req: CompletionRequest,
): Promise<JobSubmitResponse> =>
  fetchJson<JobSubmitResponse>('/api/ai/jobs', {
    method: 'POST',
    body: JSON.stringify(req),
  });

export const getJob = (jobId: string): Promise<JobStatusResponse> =>
  fetchJson<JobStatusResponse>(
    `/api/ai/jobs/${encodeURIComponent(jobId)}`,
  );

export const deleteJob = (jobId: string): Promise<void> =>
  fetchJson<undefined>(`/api/ai/jobs/${encodeURIComponent(jobId)}`, {
    method: 'DELETE',
  });
