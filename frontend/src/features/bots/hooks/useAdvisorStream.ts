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

  useEffect(() => {
    mountedRef.current = true;
    if (!botId) return;
    const activeBotId = botId;

    function connect() {
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(`${protocol}//${location.host}/ws/bots/${activeBotId}/advisor`);
      wsRef.current = ws;

      ws.onmessage = (evt) => {
        if (!mountedRef.current) return;
        try {
          const raw: unknown = JSON.parse(evt.data as string);
          if (!isAdvisorWsFrame(raw)) return;
          void queryClient.invalidateQueries({
            queryKey: ['bot', activeBotId, 'advisor-decisions'],
          });
          if (raw.verdict === 'veto') {
            useToastStore.getState().push({
              title: 'Advisor veto',
              description: raw.reasoning ?? raw.reasoning_preview ?? '',
              tone: 'error',
            });
          }
          retryRef.current = 0;
        } catch {
          // ignore malformed frames
        }
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        const delay = RETRY_DELAYS[Math.min(retryRef.current, RETRY_DELAYS.length - 1)];
        retryRef.current++;
        setTimeout(connect, delay);
      };
    }

    connect();
    return () => {
      mountedRef.current = false;
      wsRef.current?.close();
    };
  }, [botId, queryClient]);
}
