import * as React from 'react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useRiskLimits, RISK_LIMITS_QUERY_KEY } from '@/hooks/useRiskLimits';
import type { RiskLimitOut } from '@/services/risk/types';

const rowGlobal: RiskLimitOut = {
  id: 1,
  scope_type: 'global',
  scope_id: null,
  limit_kind: 'max_daily_loss_currency_base',
  limit_value: '1000.00000000',
  warn_at_pct: '80.00',
  is_active: true,
  notes: '',
  created_at: '2026-05-11T12:00:00Z',
  updated_at: '2026-05-11T12:00:00Z',
  updated_by: 'test@example.com',
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

describe('useRiskLimits', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('list query returns the parsed RiskLimitOut[]', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse([rowGlobal]));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(() => useRiskLimits(), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));
    expect(result.current.list.data).toEqual([rowGlobal]);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/risk/limits'),
      expect.objectContaining({ credentials: 'include' }),
    );
  });

  it('create mutation mints a CSRF nonce, POSTs with X-Confirm-Nonce, then invalidates the list query', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    // 1) Initial list fetch
    fetchMock.mockResolvedValueOnce(jsonResponse([rowGlobal]));
    // 2) CSRF mint
    fetchMock.mockResolvedValueOnce(jsonResponse({ nonce: 'csrf-token-abc' }));
    // 3) POST
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...rowGlobal, id: 2 }, 201));
    // 4) Refetch after invalidate
    fetchMock.mockResolvedValueOnce(jsonResponse([rowGlobal, { ...rowGlobal, id: 2 }]));

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries');

    const { result } = renderHook(() => useRiskLimits(), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));

    await act(async () => {
      await result.current.create.mutateAsync({
        scope_type: 'global',
        scope_id: null,
        limit_kind: 'max_daily_loss_currency_base',
        limit_value: '2000.00',
        warn_at_pct: null,
        is_active: true,
        notes: '',
      });
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: RISK_LIMITS_QUERY_KEY });

    // The POST call must carry the minted nonce.
    const postCall = fetchMock.mock.calls.find(
      (call) => typeof call[0] === 'string' && (call[0] as string).endsWith('/api/admin/risk-limits'),
    );
    if (!postCall) throw new Error('expected POST /api/admin/risk-limits in mock calls');
    const init = postCall[1] as RequestInit;
    expect(init.method).toBe('POST');
    const headers = new Headers(init.headers);
    expect(headers.get('X-Confirm-Nonce')).toBe('csrf-token-abc');
  });

  it('update mutation invalidates after a successful PUT', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse([rowGlobal]));
    fetchMock.mockResolvedValueOnce(jsonResponse({ nonce: 'csrf-update' }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...rowGlobal, limit_value: '5000.00000000' }));
    fetchMock.mockResolvedValueOnce(jsonResponse([{ ...rowGlobal, limit_value: '5000.00000000' }]));

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries');
    const { result } = renderHook(() => useRiskLimits(), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));

    await act(async () => {
      await result.current.update.mutateAsync({
        id: 1,
        body: {
          scope_type: 'global',
          scope_id: null,
          limit_kind: 'max_daily_loss_currency_base',
          limit_value: '5000.00',
          warn_at_pct: null,
          is_active: true,
          notes: '',
        },
      });
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: RISK_LIMITS_QUERY_KEY });
  });

  it('remove mutation invalidates after a successful DELETE', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse([rowGlobal]));
    fetchMock.mockResolvedValueOnce(jsonResponse({ nonce: 'csrf-del' }));
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    fetchMock.mockResolvedValueOnce(jsonResponse([]));

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries');
    const { result } = renderHook(() => useRiskLimits(), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));

    await act(async () => {
      await result.current.remove.mutateAsync(1);
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: RISK_LIMITS_QUERY_KEY });
  });
});
