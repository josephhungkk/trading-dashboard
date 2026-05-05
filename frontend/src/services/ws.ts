const QUOTES_WS_PATH = '/ws/quotes';

export function quotesWsUrl(apiBase: string | undefined = import.meta.env.VITE_API_BASE as string | undefined): string {
  const base = apiBase ?? (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000');
  const url = new URL(QUOTES_WS_PATH, base.endsWith('/') ? base : `${base}/`);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}

export function connectWs(): WebSocket {
  const ws = new WebSocket(quotesWsUrl(), ['msgpack-v1']);
  ws.binaryType = 'arraybuffer';
  return ws;
}
