import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { BotStatusFrame } from '../../../services/bots/types';

const RETRY_DELAYS = [500, 1500, 5000, 15000];

export function useBotStatus(): void {
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);

  useEffect(() => {
    let cancelled = false;

    function connect() {
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(`${protocol}//${location.host}/ws/bots/status`);
      wsRef.current = ws;

      ws.onmessage = (evt) => {
        try {
          const frame = JSON.parse(evt.data as string) as BotStatusFrame;
          void queryClient.invalidateQueries({ queryKey: ['bots'] });
          void queryClient.invalidateQueries({ queryKey: ['bot', frame.bot_id] });
        } catch {
          // ignore malformed frames
        }
        retryRef.current = 0;
      };

      ws.onclose = () => {
        if (cancelled) return;
        const delay = RETRY_DELAYS[Math.min(retryRef.current, RETRY_DELAYS.length - 1)];
        retryRef.current++;
        setTimeout(connect, delay);
      };
    }

    connect();
    return () => {
      cancelled = true;
      wsRef.current?.close();
    };
  }, [queryClient]);
}
