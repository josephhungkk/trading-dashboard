export interface ModifyNonceResponse {
  nonce: string;
  expires_at: string;
}

export interface OrderEventEnvelope {
  order_id: string;
  modify_id?: string;
  stop_price?: string;
  status: string;
  ts: string;
}

export interface OrderEventsHandle {
  close: () => void;
}

interface ErrorBody {
  detail?: string;
  error?: string;
}

export class ModifyNonceError extends Error {}

// HIGH-3: signal parameter allows callers (ConfirmDialog) to abort the in-flight
// fetch on unmount/close, preventing orphaned Redis nonces from accumulating.
export async function mintModifyNonce(orderId: string, signal?: AbortSignal): Promise<ModifyNonceResponse> {
  const init: RequestInit = {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ order_id: orderId }),
  };
  if (signal !== undefined) init.signal = signal;
  const res = await fetch('/api/orders/nonce/modify', init);
  if (!res.ok) throw new ModifyNonceError(`mint failed: ${res.status}`);
  return res.json() as Promise<ModifyNonceResponse>;
}

export async function submitModify(args: {
  orderId: string;
  stopPrice: number;
  nonce: string;
}): Promise<{ accepted: true } | { accepted: false; reason: string; status: number }> {
  const res = await fetch('/api/orders/modify', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      order_id: args.orderId,
      stop_price: args.stopPrice.toString(),
      nonce: args.nonce,
    }),
  });
  if (res.status === 202 || res.status === 200) return { accepted: true };
  const body = await res.json().catch(() => ({})) as ErrorBody;
  return {
    accepted: false,
    reason: body.detail ?? body.error ?? `http_${res.status}`,
    status: res.status,
  };
}

export async function getOrderState(orderId: string): Promise<{ stop_price?: string; status: string } | null> {
  const res = await fetch(`/api/orders/${encodeURIComponent(orderId)}`, {
    credentials: 'same-origin',
  });
  if (!res.ok) return null;
  return res.json() as Promise<{ stop_price?: string; status: string }>;
}

/**
 * @internal — backend /ws/orders endpoint not yet wired (Phase 10).
 * Exported for future use and tests that validate the correct future behavior.
 * Do NOT call from production UI code until the backend endpoint lands.
 */
export function subscribeOrderEvents(onEvent: (env: OrderEventEnvelope) => void): () => void {
  const handle = openOrderEvents(readJwtFromCookie, onEvent);
  return () => {
    handle.close();
  };
}

function openOrderEvents(
  getJwt: () => string | null,
  onEvent: (env: OrderEventEnvelope) => void,
): OrderEventsHandle {
  const jwt = getJwt();
  if (!jwt) return { close: () => undefined };

  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${window.location.host}/ws/orders`, [`bearer.${jwt}`]);
  let closed = false;

  ws.onopen = () => {
    ws.send(JSON.stringify({ op: 'subscribe' }));
  };

  ws.onmessage = (ev: MessageEvent) => {
    try {
      const data: unknown = JSON.parse(ev.data as string);
      if (typeof data !== 'object' || data === null) return;
      const frame = data as Record<string, unknown>;
      if (frame['op'] === 'ping') {
        ws.send(JSON.stringify({ op: 'pong' }));
        return;
      }
      if (frame['op'] === 'error') return;
      if (isOrderEventEnvelope(frame)) onEvent(frame);
    } catch {
      /* ignore malformed frames */
    }
  };

  ws.onerror = () => {
    if (!closed) ws.close();
  };

  return {
    close: () => {
      closed = true;
      ws.close();
    },
  };
}

function isOrderEventEnvelope(value: unknown): value is OrderEventEnvelope {
  if (typeof value !== 'object' || value === null) return false;
  const frame = value as Record<string, unknown>;
  return (
    typeof frame['order_id'] === 'string' &&
    typeof frame['status'] === 'string' &&
    typeof frame['ts'] === 'string' &&
    (frame['modify_id'] === undefined || typeof frame['modify_id'] === 'string') &&
    (frame['stop_price'] === undefined || typeof frame['stop_price'] === 'string')
  );
}

function readJwtFromCookie(): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie
    .split(';')
    .map((c) => c.trim())
    .find((c) => c.startsWith('cf_authorization='));
  if (!match) return null;
  return match.slice('cf_authorization='.length) || null;
}
