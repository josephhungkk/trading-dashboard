import { useEffect, useRef, useState } from 'react';
import type { AdvisorWsFrame } from '../../../services/advisor/types';

const RETRY_DELAYS = [500, 1500, 5000, 15000];
const MAX_FRAMES = 200;

function isAdvisorWsFrame(frame: unknown): frame is AdvisorWsFrame {
  return typeof frame === 'object' && frame !== null && 'v' in frame && frame.v === 1;
}

export function useAdvisorFeedStream(): {
  frames: AdvisorWsFrame[];
  isConnected: boolean;
} {
  const [frames, setFrames] = useState<AdvisorWsFrame[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);

  useEffect(() => {
    let cancelled = false;

    function connect() {
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(`${protocol}//${location.host}/ws/bots/advisor`);
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled) return;
        setIsConnected(true);
        retryRef.current = 0;
      };

      ws.onmessage = (evt) => {
        try {
          const raw: unknown = JSON.parse(evt.data as string);
          if (!isAdvisorWsFrame(raw)) return;
          setFrames((current) => [raw, ...current].slice(0, MAX_FRAMES));
          retryRef.current = 0;
        } catch {
          // ignore malformed frames
        }
      };

      ws.onclose = () => {
        if (cancelled) return;
        setIsConnected(false);
        const delay = RETRY_DELAYS[Math.min(retryRef.current, RETRY_DELAYS.length - 1)];
        retryRef.current++;
        setTimeout(connect, delay);
      };
    }

    connect();
    return () => {
      cancelled = true;
      setIsConnected(false);
      wsRef.current?.close();
    };
  }, []);

  return { frames, isConnected };
}
