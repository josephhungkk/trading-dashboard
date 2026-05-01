/**
 * Phase 7a Schwab admin service wrapper.
 * Wraps the admin endpoints + manages the OAuth-start redirect.
 */

const ADMIN = "/api/admin/brokers/schwab";

export interface SchwabTokenStatus {
  accessTokenIssuedAt: Date | null;
  refreshTokenIssuedAt: Date | null;
  tier2RefreshEnabled: boolean;
  tier2ConsecutiveFailures: number;
}

export function connectStart(win: Window = window): void {
  win.location.href = `${ADMIN}/oauth-start`;
}

export async function getTokenStatus(
  fetchFn: typeof fetch = fetch,
): Promise<SchwabTokenStatus> {
  const resp = await fetchFn(`${ADMIN}/status`, { credentials: "include" });
  if (!resp.ok) {
    throw new Error(`getTokenStatus ${resp.status}`);
  }
  const cfg: Record<string, string | boolean | number | null> = await resp.json();
  return {
    accessTokenIssuedAt: cfg.access_token_issued_at
      ? new Date(cfg.access_token_issued_at as string)
      : null,
    refreshTokenIssuedAt: cfg.refresh_token_issued_at
      ? new Date(cfg.refresh_token_issued_at as string)
      : null,
    tier2RefreshEnabled: Boolean(cfg.tier2_refresh_enabled),
    tier2ConsecutiveFailures: Number(cfg.tier2_consecutive_failures ?? 0),
  };
}

export async function postReconfigure(fetchFn: typeof fetch = fetch): Promise<void> {
  const resp = await fetchFn(`${ADMIN}/reconfigure`, {
    method: "POST",
    credentials: "include",
  });
  if (!resp.ok) throw new Error(`reconfigure ${resp.status}`);
}

export async function disconnect(
  fetchFn: typeof fetch = fetch,
  opts: { deleteCredentials: boolean } = { deleteCredentials: false },
): Promise<void> {
  const params = new URLSearchParams({
    delete_credentials: String(opts.deleteCredentials),
  });
  const resp = await fetchFn(`${ADMIN}/disconnect?${params}`, {
    method: "POST",
    credentials: "include",
  });
  if (!resp.ok) throw new Error(`disconnect ${resp.status}`);
}

export async function enableTier2(
  fetchFn: typeof fetch = fetch,
  enabled: boolean,
): Promise<void> {
  const resp = await fetchFn(
    `/api/admin/config/broker/schwab.tier2_refresh_enabled`,
    {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: String(enabled), value_type: "bool" }),
    },
  );
  if (!resp.ok) throw new Error(`tier2 enable ${resp.status}`);
}

export function subscribeConfigStream(
  ns: string,
  onMessage: (data: unknown) => void,
): () => void {
  const es = new EventSource(
    `/api/admin/config/stream?ns=${encodeURIComponent(ns)}`,
    { withCredentials: true },
  );
  es.onmessage = (ev) => {
    try {
      onMessage(JSON.parse(ev.data));
    } catch {
      /* keepalive */
    }
  };
  return () => es.close();
}
