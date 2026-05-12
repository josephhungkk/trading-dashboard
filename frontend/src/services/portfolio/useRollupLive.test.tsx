/**
 * Phase 10b.2 — useRollupLive hook tests.
 * Mocks fetch via spyOn(api, 'fetchRollupLive') and the global WebSocket
 * constructor (jsdom doesn't ship one). 4 tests:
 *   1. initial REST fetch hydrates cache
 *   2. WS snapshot frame overwrites cache via setQueryData
 *   3. unknown frame version closes the WS
 *   4. malformed JSON frame is silently ignored
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/services/portfolio/api';
import type { RollupLive } from '@/services/portfolio/types';
import { useRollupLive } from '@/services/portfolio/useRollupLive';

function makeRollup(overrides: Partial<RollupLive> = {}): RollupLive {
  return {
    base_currency: 'GBP',
    total_nlv_base: '1000.00',
    total_realized_today_base: '0',
    total_unrealized_base: '0',
    history_since: '2026-05-12T00:00:00+00:00',
    accounts: [],
    exposure_by_asset_class: [],
    fx_rates: {},
    stale_accounts: [],
    fx_stale_accounts: [],
    partial: false,
    ...overrides,
  } as RollupLive;
}

interface FakeWS {
  onopen: ((ev: Event) => void) | null;
  onmessage: ((ev: MessageEvent<string>) => void) | null;
  onclose: ((ev: CloseEvent) => void) | null;
  onerror: ((ev: Event) => void) | null;
  close: ReturnType<typeof vi.fn>;
}

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  }
  return Wrapper;
}

describe('useRollupLive', () => {
  let ws: FakeWS;

  beforeEach(() => {
    ws = {
      onopen: null,
      onmessage: null,
      onclose: null,
      onerror: null,
      close: vi.fn(),
    };
    // jsdom has no WebSocket; provide a class whose construction returns
    // our fake. `new FakeWebSocket(...)` MUST return an object — using a
    // class with a constructor that copies fields onto `this` is the
    // cleanest way and lets `new` resolve normally.
    class FakeWebSocket {
      onopen: FakeWS['onopen'] = null;
      onmessage: FakeWS['onmessage'] = null;
      onclose: FakeWS['onclose'] = null;
      onerror: FakeWS['onerror'] = null;
      close = ws.close;
      constructor(_url: string) {  // eslint-disable-line @typescript-eslint/no-unused-vars
        // Wire our outer `ws` capture to point at this instance's slots
        // so the test can drive onmessage / onopen externally.
        ws = this as unknown as FakeWS;
      }
    }
    vi.stubGlobal('WebSocket', FakeWebSocket);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('initial REST fetch hydrates the cache', async () => {
    vi.spyOn(api, 'fetchRollupLive').mockResolvedValue(makeRollup());

    const { result } = renderHook(() => useRollupLive('GBP'), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.base_currency).toBe('GBP');
    expect(result.current.data?.total_nlv_base).toBe('1000.00');
  });

  it('WS snapshot frame overwrites cache via setQueryData', async () => {
    vi.spyOn(api, 'fetchRollupLive').mockResolvedValue(makeRollup());

    const { result } = renderHook(() => useRollupLive('GBP'), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.data).toBeDefined());

    // Simulate a snapshot frame arriving on the WS.
    ws.onmessage?.({
      data: JSON.stringify({
        version: 1,
        type: 'snapshot',
        payload: makeRollup({ total_nlv_base: '9999.99' }),
      }),
    } as MessageEvent<string>);

    await waitFor(() =>
      expect(result.current.data?.total_nlv_base).toBe('9999.99'),
    );
  });

  it('unknown frame version closes the WS', async () => {
    vi.spyOn(api, 'fetchRollupLive').mockResolvedValue(makeRollup());

    renderHook(() => useRollupLive('GBP'), { wrapper: makeWrapper() });

    ws.onmessage?.({
      data: JSON.stringify({ version: 99, type: 'snapshot' }),
    } as MessageEvent<string>);

    expect(ws.close).toHaveBeenCalled();
  });

  it('malformed JSON frame is silently ignored', async () => {
    vi.spyOn(api, 'fetchRollupLive').mockResolvedValue(makeRollup());

    const { result } = renderHook(() => useRollupLive('GBP'), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.data).toBeDefined());

    // Throw inside JSON.parse — the handler must swallow it.
    expect(() =>
      ws.onmessage?.({ data: 'not json{{{' } as MessageEvent<string>),
    ).not.toThrow();

    // Cache value is unchanged.
    expect(result.current.data?.total_nlv_base).toBe('1000.00');
  });
});
