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
  jwt: string,
  onMessage: (env: BarEnvelope) => void,
  onReconnect?: () => void, // callback to refetch trailing 2 closed buckets via REST
): LiveTailHandle {
  let ws: WebSocket | null = null;
  let backoff = 1000;
  let closed = false;

  function connect(): void {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${proto}://${window.location.host}/ws/bars/${canonicalId}/${timeframe}`;
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
        if (frame['op'] === 'error') return; // silently ignore protocol errors
        onMessage(data as BarEnvelope);
      } catch {
        /* ignore malformed frames */
      }
    };

    ws.onclose = () => {
      if (closed) return;
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
