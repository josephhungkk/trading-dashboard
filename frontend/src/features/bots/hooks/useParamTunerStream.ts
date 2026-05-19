import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import type { TunerWsFrame } from '@/services/param_tuner/types';

const RETRY_DELAYS = [500, 1500, 5000, 15000];

function isTunerWsFrame(frame: unknown): frame is TunerWsFrame {
  return typeof frame === 'object' && frame !== null && 'v' in frame && frame.v === 1;
}

export function useParamTunerStream(botId: string | undefined): void {
  const queryClient = useQueryClient();
  const mountedRef = useRef(true);
  const retryRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    if (!botId) return;
    const activeBotId = botId;
    let cancelled = false;

    function connect() {
      if (cancelled) return;
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(`${protocol}//${location.host}/ws/bots/${activeBotId}/tuner`);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current || cancelled) return;
        retryRef.current = 0;
      };

      ws.onmessage = (evt) => {
        if (!mountedRef.current || cancelled) return;
        try {
          const raw: unknown = JSON.parse(evt.data as string);
          if (!isTunerWsFrame(raw)) return;
          if (raw.type === 'ranked' || raw.type === 'applied') {
            void queryClient.invalidateQueries({
              queryKey: ['param-suggestions', activeBotId],
            });
          }
        } catch {
          // ignore malformed frames
        }
      };

      ws.onclose = () => {
        if (!mountedRef.current || cancelled) return;
        const delay = RETRY_DELAYS[Math.min(retryRef.current, RETRY_DELAYS.length - 1)];
        retryRef.current++;
        retryTimerRef.current = setTimeout(connect, delay);
      };
    }

    connect();

    return () => {
      cancelled = true;
      mountedRef.current = false;
      if (retryTimerRef.current !== null) clearTimeout(retryTimerRef.current);
      wsRef.current?.close();
    };
  }, [botId, queryClient]);
}
