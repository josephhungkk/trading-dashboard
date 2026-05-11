import * as React from 'react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor, act } from '@testing-library/react';
import {
  useAccountKillSwitch,
  accountKillSwitchQueryKey,
} from '@/hooks/useAccountKillSwitch';
import type { AccountKillSwitchOut } from '@/services/risk/types';

const ACCOUNT_ID = '00000000-0000-4000-8000-000000000001';

const enabledRow: AccountKillSwitchOut = {
  account_id: ACCOUNT_ID,
  is_enabled: true,
  reason: 'manual freeze',
  enabled_at: '2026-05-11T12:00:00Z',
  enabled_by: 'admin@example.com',
  updated_at: '2026-05-11T12:00:00Z',
};

interface WrapperProps {
  children: React.ReactNode;
}

function makeWrapper(client: QueryClient): React.FC<WrapperProps> {
  return function HookWrapper(props: WrapperProps) {
    return <QueryClientProvider client={client}>{props.children}</QueryClientProvider>;
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('useAccountKillSwitch', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('query returns null when the GET 404s (switch implicitly off)', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: { error: 'kill_switch_not_set' } }, 404),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(() => useAccountKillSwitch(ACCOUNT_ID), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.query.isSuccess).toBe(true));
    expect(result.current.query.data).toBeNull();
  });

  it('query returns the row when present', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse(enabledRow));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(() => useAccountKillSwitch(ACCOUNT_ID), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.query.isSuccess).toBe(true));
    expect(result.current.query.data).toEqual(enabledRow);
  });

  it('setKillSwitch mints a CSRF nonce, POSTs with X-Confirm-Nonce, and invalidates the per-account query', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    // 1) Initial GET — 404 (off)
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: { error: 'kill_switch_not_set' } }, 404));
    // 2) CSRF mint
    fetchMock.mockResolvedValueOnce(jsonResponse({ nonce: 'csrf-ks' }));
    // 3) POST toggle
    fetchMock.mockResolvedValueOnce(jsonResponse(enabledRow));
    // 4) Refetch after invalidate
    fetchMock.mockResolvedValueOnce(jsonResponse(enabledRow));

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries');

    const { result } = renderHook(() => useAccountKillSwitch(ACCOUNT_ID), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.query.isFetched).toBe(true));

    await act(async () => {
      await result.current.setKillSwitch.mutateAsync({
        is_enabled: true,
        reason: 'manual freeze',
      });
    });

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: accountKillSwitchQueryKey(ACCOUNT_ID),
    });

    const postCall = fetchMock.mock.calls.find((call) => {
      const url = call[0];
      const init = call[1] as RequestInit | undefined;
      return (
        typeof url === 'string' &&
        url.includes('/api/admin/accounts/') &&
        url.endsWith('/kill-switch') &&
        init?.method === 'POST'
      );
    });
    if (!postCall) throw new Error('expected POST kill-switch in mock calls');
    const init = postCall[1] as RequestInit;
    expect(init.method).toBe('POST');
    const headers = new Headers(init.headers);
    expect(headers.get('X-Confirm-Nonce')).toBe('csrf-ks');
  });

  it('query is disabled when accountId is empty', () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(() => useAccountKillSwitch(''), {
      wrapper: makeWrapper(client),
    });
    expect(result.current.query.fetchStatus).toBe('idle');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
