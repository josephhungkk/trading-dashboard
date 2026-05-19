import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useToastStore } from '../../../hooks/use-toast';
import type { AdvisorWsFrame } from '../../../services/advisor/types';

const RETRY_DELAYS = [500, 1500, 5000, 15000];

function isAdvisorWsFrame(frame: unknown): frame is AdvisorWsFrame {
  return typeof frame === 'object' && frame !== null && 'v' in frame && frame.v === 1;
}

export function useAdvisorStream(botId: string | undefined): void {
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);
  const mountedRef = useRef(true);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Per-symbol veto debounce: canonical_id → timer
  const vetoTimerRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  useEffect(() => {
    mountedRef.current = true;
    if (!botId) return;
    const activeBotId = botId;

    function connect() {
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(`${protocol}//${location.host}/ws/bots/${activeBotId}/advisor`);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        retryRef.current = 0;
      };

      ws.onmessage = (evt) => {
        if (!mountedRef.current) return;
        try {
          const raw: unknown = JSON.parse(evt.data as string);
          if (!isAdvisorWsFrame(raw)) return;
          if (raw.type !== 'decision') return;
          void queryClient.invalidateQueries({
            queryKey: ['bot', activeBotId, 'advisor-decisions'],
          });
          if (raw.verdict === 'veto') {
            const symbol = raw.canonical_id;
            const existing = vetoTimerRef.current.get(symbol);
            if (existing !== undefined) clearTimeout(existing);
            const timer = setTimeout(() => {
              vetoTimerRef.current.delete(symbol);
              useToastStore.getState().push({
                title: 'Advisor veto',
                description: raw.reasoning ?? raw.reasoning_preview ?? '',
                tone: 'error',
              });
            }, 200);
            vetoTimerRef.current.set(symbol, timer);
          }
        } catch {
          // ignore malformed frames
        }
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        const delay = RETRY_DELAYS[Math.min(retryRef.current, RETRY_DELAYS.length - 1)];
        retryRef.current++;
        retryTimerRef.current = setTimeout(connect, delay);
      };
    }

    connect();
    const vetoTimers = vetoTimerRef.current;
    return () => {
      mountedRef.current = false;
      if (retryTimerRef.current !== null) clearTimeout(retryTimerRef.current);
      for (const t of vetoTimers.values()) clearTimeout(t);
      vetoTimers.clear();
      wsRef.current?.close();
    };
  }, [botId, queryClient]);
}
