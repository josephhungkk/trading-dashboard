import { mintCsrfNonce } from '@/services/admin/api';
import type { FxPair, FxQuote, FxQuoteRequest, FxAcceptRequest } from './types';

const BASE = '/api/forex';

export async function listPairs(): Promise<FxPair[]> {
  const res = await fetch(`${BASE}/pairs`, { credentials: 'include' });
  if (!res.ok) throw new Error('Failed to fetch FX pairs');
  return res.json() as Promise<FxPair[]>;
}

export async function requestQuote(req: FxQuoteRequest): Promise<FxQuote> {
  const res = await fetch(`${BASE}/quote`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<FxQuote>;
}

export async function acceptQuote(
  brokerQuoteId: string,
  req: FxAcceptRequest,
): Promise<{ order_id: string; fill_price: string; status: string }> {
  const nonce = await mintCsrfNonce();
  const res = await fetch(`${BASE}/quote/${brokerQuoteId}/accept`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-Csrf-Nonce': nonce },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ order_id: string; fill_price: string; status: string }>;
}

export async function cancelQuote(brokerQuoteId: string, accountId: string): Promise<void> {
  await fetch(`${BASE}/quote/${brokerQuoteId}?account_id=${accountId}`, {
    method: 'DELETE',
    credentials: 'include',
  });
}

export async function listQuotes(accountId: string): Promise<FxQuote[]> {
  const res = await fetch(`${BASE}/quotes?account_id=${accountId}`, { credentials: 'include' });
  if (!res.ok) throw new Error('Failed to fetch quotes');
  return res.json() as Promise<FxQuote[]>;
}
