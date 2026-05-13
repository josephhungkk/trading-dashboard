/**
 * Phase 11a-D — AI async job hook.
 * REST polling owns the baseline state; websocket pushes update the same
 * TanStack Query cache and polling resumes automatically on WS disconnect.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';

import { deleteJob, getJob } from '@/services/ai/api';
import type { JobStatusResponse, JobWsFrame } from '@/services/ai/types';

const POLL_INTERVAL_MS = 10_000;
const RECONNECT_DELAYS_MS = [500, 1_500, 5_000, 15_000];
const TERMINAL_STATES = new Set(['completed', 'failed', 'cancelled']);

export interface UseAiJobReturn {
  status: string | undefined;
  response: Record<string, unknown> | null;
  error: string | null;
  cancelRequested: boolean;
  cancel: () => Promise<void>;
}

function defaultWsUrl(jobId: string): string {
  if (typeof window === 'undefined') return `/ws/ai/jobs/${encodeURIComponent(jobId)}`;
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${window.location.host}/ws/ai/jobs/${encodeURIComponent(jobId)}`;
}

function statusOf(data: JobStatusResponse | undefined): string | undefined {
  return data?.status ?? data?.state;
}

function isTerminal(data: JobStatusResponse | undefined): boolean {
  const status = statusOf(data);
  return status !== undefined && TERMINAL_STATES.has(status);
}

function fromWsFrame(frame: JobWsFrame): JobStatusResponse {
  return {
    job_id: frame.job_id,
    status: frame.state,
    state: frame.state,
    ...(frame.error_code !== undefined ? { error_code: frame.error_code } : {}),
    ...(frame.model !== undefined ? { model: frame.model } : {}),
    ...(frame.response !== undefined ? { response: frame.response } : {}),
    ...(frame.fallback_chain !== undefined ? { fallback_chain: frame.fallback_chain } : {}),
  };
}

export function useAiJob(job_id: string | undefined): UseAiJobReturn {
  const qc = useQueryClient();
  const wsConnectedRef = useRef(false);
  const mountedRef = useRef(true);
  const [cancelState, setCancelState] = useState<{
    jobId: string | undefined;
    requested: boolean;
  }>({ jobId: undefined, requested: false });

  const query = useQuery<JobStatusResponse>({
    queryKey: ['ai', 'job', job_id],
    queryFn: () => {
      if (job_id === undefined) throw new Error('missing_job_id');
      return getJob(job_id);
    },
    enabled: job_id !== undefined,
    refetchInterval: (queryState) => {
      const data = queryState.state.data as JobStatusResponse | undefined;
      if (wsConnectedRef.current || isTerminal(data)) return false;
      return POLL_INTERVAL_MS;
    },
  });

  useEffect(() => {
    mountedRef.current = true;
    if (typeof window === 'undefined' || job_id === undefined) return undefined;

    let currentWs: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    const connect = (): void => {
      if (!mountedRef.current) return;
      const ws = new WebSocket(defaultWsUrl(job_id));
      currentWs = ws;
      let downHandled = false;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        attempt = 0;
        wsConnectedRef.current = true;
      };

      ws.onmessage = (e: MessageEvent<string>) => {
        if (!mountedRef.current) return;
        try {
          const frame = JSON.parse(e.data) as JobWsFrame;
          if (frame.version !== 1) {
            ws.close();
            return;
          }
          if (frame.type === 'state') {
            qc.setQueryData<JobStatusResponse>(
              ['ai', 'job', job_id],
              fromWsFrame(frame),
            );
          }
        } catch {
          // Malformed frames are ignored; REST polling covers missed state.
        }
      };

      const onDown = (): void => {
        if (!mountedRef.current || downHandled) return;
        downHandled = true;
        wsConnectedRef.current = false;
        const delay = RECONNECT_DELAYS_MS[attempt];
        if (delay !== undefined) {
          attempt += 1;
          reconnectTimer = setTimeout(connect, delay);
        }
      };

      ws.onclose = onDown;
      ws.onerror = onDown;
    };

    connect();

    return () => {
      mountedRef.current = false;
      wsConnectedRef.current = false;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      if (currentWs !== null) currentWs.close();
    };
  }, [job_id, qc]);

  const cancel = useCallback(async (): Promise<void> => {
    if (job_id === undefined) return;
    if (mountedRef.current) {
      setCancelState({ jobId: job_id, requested: true });
    }
    await deleteJob(job_id);
  }, [job_id]);

  const cancelRequested = cancelState.jobId === job_id && cancelState.requested;

  return {
    status: statusOf(query.data),
    response: query.data?.response ?? null,
    error: query.data?.error_code ?? ((query.error as Error | null)?.message ?? null),
    cancelRequested,
    cancel,
  };
}
