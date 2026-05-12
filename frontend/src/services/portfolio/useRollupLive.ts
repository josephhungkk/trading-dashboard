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

export interface UseRollupLiveState {
  data: RollupLive | undefined;
  isLoading: boolean;
  error: Error | null;
  wsConnected: boolean;
}

export function useRollupLive(base: BaseCurrency): UseRollupLiveState {
  const qc = useQueryClient();
  const wsConnectedRef = useRef(false);
  const [wsConnected, setWsConnected] = useState(false);

  const query = useQuery<RollupLive>({
    queryKey: ['portfolio', 'rollup', base],
    queryFn: () => fetchRollupLive(base),
    refetchInterval: () => (wsConnectedRef.current ? false : POLL_INTERVAL_MS),
  });

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${scheme}://${window.location.host}/ws/portfolio/rollup?base=${base}`;
    const ws = new WebSocket(url);

    ws.onopen = () => {
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
        // 'stale' frames are advisory; the next snapshot carries the
        // authoritative payload. Surfacing them is the consumer's job
        // (RollupKpiBar can read fx_stale_accounts off the snapshot).
      } catch {
        // Malformed frame — ignore and rely on poll fallback.
      }
    };
    ws.onclose = () => {
      wsConnectedRef.current = false;
      setWsConnected(false);
    };
    ws.onerror = () => {
      // Browser raises 'error' BEFORE 'close'; clear connected flag
      // immediately so the next refetchInterval evaluation switches to poll.
      wsConnectedRef.current = false;
      setWsConnected(false);
    };

    return () => {
      ws.close();
    };
  }, [base, qc]);

  return {
    data: query.data,
    isLoading: query.isLoading,
    error: (query.error ?? null) as Error | null,
    wsConnected,
  };
}
