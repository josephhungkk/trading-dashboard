import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ModifyNonceError,
  getOrderState,
  mintModifyNonce,
  submitModify,
} from './orders';

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json', ...init.headers },
    ...init,
  });
}

function stubFetch(response: Response): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(() => Promise.resolve(response));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('chart orders service', () => {
  it('mintModifyNonce posts to correct URL with order_id', async () => {
    const body = { nonce: 'nonce-1', expires_at: '2026-05-08T12:00:30Z' };
    const fetchMock = stubFetch(jsonResponse(body));

    await expect(mintModifyNonce('leg-1')).resolves.toEqual(body);

    expect(fetchMock).toHaveBeenCalledWith('/api/orders/nonce/modify', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order_id: 'leg-1' }),
    });
  });

  it('submitModify posts with order_id, stop_price, and nonce', async () => {
    const fetchMock = stubFetch(jsonResponse({}, { status: 202 }));

    await submitModify({ orderId: 'leg-1', stopPrice: 184.99, nonce: 'nonce-1' });

    expect(fetchMock).toHaveBeenCalledWith('/api/orders/modify', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        order_id: 'leg-1',
        stop_price: '184.99',
        nonce: 'nonce-1',
      }),
    });
  });

  it('submitModify returns accepted true on 202', async () => {
    stubFetch(jsonResponse({}, { status: 202 }));

    await expect(submitModify({ orderId: 'leg-1', stopPrice: 184.99, nonce: 'nonce-1' }))
      .resolves.toEqual({ accepted: true });
  });

  it('submitModify returns accepted false with reason on 412', async () => {
    stubFetch(jsonResponse({ detail: 'nonce_invalid_or_expired' }, { status: 412 }));

    await expect(submitModify({ orderId: 'leg-1', stopPrice: 184.99, nonce: 'nonce-1' }))
      .resolves.toEqual({
        accepted: false,
        reason: 'nonce_invalid_or_expired',
        status: 412,
      });
  });

  it('getOrderState returns null on 404', async () => {
    stubFetch(jsonResponse({ detail: 'not found' }, { status: 404 }));

    await expect(getOrderState('leg-1')).resolves.toBeNull();
  });

  it('mint nonce 4xx throws ModifyNonceError', async () => {
    stubFetch(jsonResponse({ detail: 'bad order' }, { status: 422 }));

    await expect(mintModifyNonce('leg-1')).rejects.toBeInstanceOf(ModifyNonceError);
  });
});
