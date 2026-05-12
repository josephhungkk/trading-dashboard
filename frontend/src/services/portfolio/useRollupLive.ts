/**
 * Phase 10b.2 §6 — hybrid REST + WS hook for portfolio rollup.
 *
 * Strategy: TanStack-Query owns the cache. When the WS is connected,
 * incoming snapshot frames overwrite the cache via setQueryData (no REST
 * fetch needed). When the WS drops, refetchInterval kicks in as a 10 s
 * poll fallback. Unknown frame schemas (version != 1) close the WS so
 * a misbehaving server falls back to REST poll automatically.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { fetchRollupLive } from '@/services/portfolio/api';
import type {
  BaseCurrency,
  RollupLive,
  RollupWsFrame,
} from '@/services/portfolio/types';

const POLL_INTERVAL_MS = 10_000;
const RECONNECT_DELAYS_MS = [500, 1_500, 5_000, 15_000]; // 4 attempts, bounded

export interface UseRollupLiveState {
  data: RollupLive | undefined;
  isLoading: boolean;
  error: Error | null;
  wsConnected: boolean;
}

export function useRollupLive(base: BaseCurrency): UseRollupLiveState {
  const qc = useQueryClient();
  const wsConnectedRef = useRef(false);
  const mountedRef = useRef(true);
  const [wsConnected, setWsConnected] = useState(false);

  const query = useQuery<RollupLive>({
    queryKey: ['portfolio', 'rollup', base],
    queryFn: () => fetchRollupLive(base),
    refetchInterval: () => (wsConnectedRef.current ? false : POLL_INTERVAL_MS),
  });

  useEffect(() => {
    mountedRef.current = true;
    if (typeof window === 'undefined') return;

    let currentWs: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    const connect = (): void => {
      if (!mountedRef.current) return;
      const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${scheme}://${window.location.host}/ws/portfolio/rollup?base=${encodeURIComponent(base)}`;
      const ws = new WebSocket(url);
      currentWs = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        attempt = 0; // reset backoff on successful connect
        wsConnectedRef.current = true;
        setWsConnected(true);
      };
      ws.onmessage = (e: MessageEvent<string>) => {
        try {
          const frame = JSON.parse(e.data) as RollupWsFrame;
          if (frame.version !== 1) {
            ws.close();
            return;
          }
          if (frame.type === 'snapshot' && frame.payload) {
            qc.setQueryData<RollupLive>(
              ['portfolio', 'rollup', base],
              frame.payload,
            );
          }
        } catch {
          // Malformed frame — ignore; the 10s REST poll covers gaps.
        }
      };
      const onDown = (): void => {
        if (!mountedRef.current) return;
        wsConnectedRef.current = false;
        setWsConnected(false);
        // Bounded exponential backoff: 500ms / 1.5s / 5s / 15s, then give up
        // and rely on the REST poll fallback (reviewer HIGH).
        const delay = RECONNECT_DELAYS_MS[attempt];
        if (delay !== undefined && mountedRef.current) {
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
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      if (currentWs !== null) currentWs.close();
    };
  }, [base, qc]);

  return {
    data: query.data,
    isLoading: query.isLoading,
    error: (query.error ?? null) as Error | null,
    wsConnected,
  };
}
