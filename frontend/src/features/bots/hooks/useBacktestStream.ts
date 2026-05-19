import { useEffect, useRef } from 'react';
import type { BacktestProgressFrame, BacktestReport } from '../../../services/backtests/types';

const RETRY_DELAYS = [500, 1500, 5000, 15000];

interface Options {
  botId: string;
  jobId: string | null;
  onProgress: (pct: number, tradesSoFar: number, barTs: string) => void;
  onDone: (report: BacktestReport) => void;
  onFailed: (errorMsg: string) => void;
}

export function useBacktestStream({
  botId,
  jobId,
  onProgress,
  onDone,
  onFailed,
}: Options): void {
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    if (!jobId) return;
    const activeJobId = jobId;

    function connect() {
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(
        `${protocol}//${location.host}/ws/bots/${botId}/backtest/${activeJobId}`,
      );
      wsRef.current = ws;

      ws.onmessage = (evt) => {
        if (!mountedRef.current) return;
        try {
          const raw: unknown = JSON.parse(evt.data as string);
          if (typeof raw !== 'object' || raw === null || !('type' in raw)) return;
          const frame = raw as BacktestProgressFrame;
          if (frame.type === 'progress') {
            onProgress(frame.pct, frame.trades_so_far, frame.current_bar_ts);
            retryRef.current = 0;
          } else if (frame.type === 'done') {
            onDone(frame.report);
            ws.close();
          } else if (frame.type === 'failed') {
            onFailed(frame.error_msg);
            ws.close();
          }
          // heartbeat: no-op
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
  }, [botId, jobId, onProgress, onDone, onFailed]);
}
