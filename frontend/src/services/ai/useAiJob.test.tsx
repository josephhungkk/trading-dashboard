import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/services/ai/api';
import type { JobStatusResponse } from '@/services/ai/types';
import { useAiJob } from '@/services/ai/useAiJob';

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

function installWebSocketMock(): FakeWS[] {
  const sockets: FakeWS[] = [];
  class FakeWebSocket {
    onopen: FakeWS['onopen'] = null;
    onmessage: FakeWS['onmessage'] = null;
    onclose: FakeWS['onclose'] = null;
    onerror: FakeWS['onerror'] = null;
    close = vi.fn();

    constructor(url: string) {
      void url;
      sockets.push(this as unknown as FakeWS);
    }
  }
  vi.stubGlobal('WebSocket', FakeWebSocket);
  return sockets;
}

describe('useAiJob', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('fetches initial job status from REST', async () => {
    installWebSocketMock();
    vi.spyOn(api, 'getJob').mockResolvedValue({
      job_id: 'job-1',
      status: 'warming',
    });

    const { result } = renderHook(() => useAiJob('job-1'), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.status).toBe('warming'));
  });

  it('applies websocket state before another poll', async () => {
    const sockets = installWebSocketMock();
    vi.spyOn(api, 'getJob').mockResolvedValue({
      job_id: 'job-1',
      status: 'warming',
    });

    const { result } = renderHook(() => useAiJob('job-1'), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.status).toBe('warming'));
    const ws = sockets[0];
    if (ws === undefined) throw new Error('websocket was not created');

    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({
          version: 1,
          type: 'state',
          job_id: 'job-1',
          state: 'inferring',
        }),
      } as MessageEvent<string>);
    });

    await waitFor(() => expect(result.current.status).toBe('inferring'));
  });

  it('stores completed websocket response and stops polling', async () => {
    vi.useFakeTimers();
    const sockets = installWebSocketMock();
    const getJob = vi.spyOn(api, 'getJob').mockResolvedValue({
      job_id: 'job-1',
      status: 'warming',
    } satisfies JobStatusResponse);

    const { result } = renderHook(() => useAiJob('job-1'), {
      wrapper: makeWrapper(),
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.status).toBe('warming');
    const ws = sockets[0];
    if (ws === undefined) throw new Error('websocket was not created');

    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({
          version: 1,
          type: 'state',
          job_id: 'job-1',
          state: 'completed',
          response: { text: 'done' },
        }),
      } as MessageEvent<string>);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.status).toBe('completed');
    expect(result.current.response).toEqual({ text: 'done' });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(getJob).toHaveBeenCalledTimes(1);
  });

  it('optimistically marks cancel requested and issues DELETE', async () => {
    installWebSocketMock();
    vi.spyOn(api, 'getJob').mockResolvedValue({
      job_id: 'job-1',
      status: 'inferring',
    });
    const deleteJob = vi.spyOn(api, 'deleteJob').mockResolvedValue(undefined);

    const { result } = renderHook(() => useAiJob('job-1'), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.status).toBe('inferring'));

    await act(async () => {
      await result.current.cancel();
    });

    expect(result.current.cancelRequested).toBe(true);
    expect(deleteJob).toHaveBeenCalledWith('job-1');
  });
});
