export interface BarEnvelope {
  canonical_id: string;
  timeframe: string;
  bucket_start: string; // ISO8601
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  trade_count: number;
  revision: number;
  partial: boolean;
}

export interface LiveTailHandle {
  close: () => void;
}

export function openLiveTail(
  canonicalId: string,
  timeframe: string,
  // HIGH-5: accept a callback so each reconnect reads a fresh JWT.
  getJwt: () => string | null,
  onMessage: (env: BarEnvelope) => void,
  onReconnect?: () => void, // callback to refetch trailing 2 closed buckets via REST
  // MED-4: surface protocol error frames to caller; no-op by default.
  onError?: (frame: Record<string, unknown>) => void,
): LiveTailHandle {
  let ws: WebSocket | null = null;
  let backoff = 1000;
  let closed = false;

  function connect(): void {
    // HIGH-5: re-read JWT on every connect attempt; abort if missing.
    const jwt = getJwt();
    if (!jwt) {
      // No credentials available — do not attempt connection.
      return;
    }

    // HIGH-6: URL-encode both path segments to handle special chars safely.
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${proto}://${window.location.host}/ws/bars/${encodeURIComponent(canonicalId)}/${encodeURIComponent(timeframe)}`;
    ws = new WebSocket(url, [`bearer.${jwt}`]);

    ws.onopen = () => {
      backoff = 1000;
      ws?.send(JSON.stringify({ op: 'subscribe', canonical_id: canonicalId, timeframe }));
      onReconnect?.();
    };

    ws.onmessage = (ev: MessageEvent) => {
      try {
        const data: unknown = JSON.parse(ev.data as string);
        if (typeof data !== 'object' || data === null) return;
        const frame = data as Record<string, unknown>;
        if (frame['op'] === 'ping') {
          ws?.send(JSON.stringify({ op: 'pong' }));
          return;
        }
        if (frame['op'] === 'error') {
          // MED-4: surface error frames to caller instead of silently dropping.
          onError?.(frame);
          return;
        }
        onMessage(data as BarEnvelope);
      } catch {
        /* ignore malformed frames */
      }
    };

    ws.onclose = (ev: CloseEvent) => {
      if (closed) return;
      // MED-1: code 4001 = auth failure — do not retry, set closed flag.
      if (ev.code === 4001) {
        closed = true;
        return;
      }
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 30_000);
    };

    ws.onerror = () => {
      ws?.close();
    };
  }

  connect();

  return {
    close: () => {
      closed = true;
      ws?.close();
    },
  };
}
