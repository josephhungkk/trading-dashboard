import type { CryptoAsset, OrderBookSnapshot } from './types';

const BASE = '/api/crypto';

export async function listAssets(accountId: string): Promise<CryptoAsset[]> {
  const res = await fetch(`${BASE}/assets?account_id=${encodeURIComponent(accountId)}`, { credentials: 'include' });
  if (!res.ok) throw new Error('Failed to fetch crypto assets');
  return res.json() as Promise<CryptoAsset[]>;
}

export async function resolveInstrument(symbol: string): Promise<{ id: number } | null> {
  const res = await fetch(`${BASE}/instrument/${encodeURIComponent(symbol)}`, { credentials: 'include' });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error('Failed to resolve crypto instrument');
  return res.json() as Promise<{ id: number }>;
}

export function subscribeOrderBook(
  canonicalId: string,
  onSnapshot: (s: OrderBookSnapshot) => void,
  onDeltas: (deltas: { side: string; price: string; qty: string; seq: number }[]) => void,
): () => void {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws/crypto/book/${encodeURIComponent(canonicalId)}`);
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data as string) as { type: string } & Record<string, unknown>;
    if (msg.type === 'book_snapshot') onSnapshot(msg as unknown as OrderBookSnapshot);
    else if (msg.type === 'book_deltas') {
      onDeltas((msg.deltas as { side: string; price: string; qty: string; seq: number }[]) ?? []);
    }
  };
  return () => ws.close();
}
