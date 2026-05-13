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

const CREDENTIALS_POLICY: RequestCredentials = isSameOriginBase(BASE) ? 'include' : 'omit';

export class AdminApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown) {
    super(`admin api ${status}`);
    this.status = status;
    this.body = body;
  }
}

export async function mintCsrfNonce(): Promise<string> {
  const r = await fetch(`${BASE}/api/admin/csrf/issue`, {
    method: 'POST',
    credentials: CREDENTIALS_POLICY,
    headers: { 'Content-Type': 'application/json' },
  });
  if (!r.ok) {
    const body: unknown = await r.json().catch(() => ({}));
    throw new AdminApiError(r.status, body);
  }
  const json = (await r.json()) as { nonce: string };
  return json.nonce;
}

export async function adminFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: CREDENTIALS_POLICY,
    headers: {
      'Content-Type': 'application/json',
      ...(init.headers ?? {}),
    },
  });
  if (!r.ok) {
    const body: unknown = await r.json().catch(() => ({}));
    throw new AdminApiError(r.status, body);
  }
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}
