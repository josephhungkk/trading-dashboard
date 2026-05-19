import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import type { ShadowWsFrame } from '@/services/shadow_promoter/types';

const RETRY_DELAYS = [500, 1500, 5000, 15000];

function isShadowWsFrame(frame: unknown): frame is ShadowWsFrame {
  return typeof frame === 'object' && frame !== null && 'v' in frame && frame.v === 1;
}

export function useShadowStream(botId: string | undefined): void {
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
      const ws = new WebSocket(`${protocol}//${location.host}/ws/bots/${activeBotId}/shadow`);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current || cancelled) return;
        retryRef.current = 0;
      };

      ws.onmessage = (evt) => {
        if (!mountedRef.current || cancelled) return;
        try {
          const raw: unknown = JSON.parse(evt.data as string);
          if (!isShadowWsFrame(raw)) return;
          if (raw.type === 'comparison') {
            void queryClient.invalidateQueries({
              queryKey: ['shadow-comparison', activeBotId],
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
