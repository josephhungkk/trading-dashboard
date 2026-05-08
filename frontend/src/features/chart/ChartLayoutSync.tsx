import * as React from 'react';
import { useEffect, useRef } from 'react';
import { pushLayout, type LayoutPayload, type LayoutSyncResult } from './services/layoutSync';
import { useChartStore } from './stores/chartStore';

interface ChartLayoutSyncProps {
  instrumentId: number | null;
  onConflict?: (remote: { payload: Record<string, unknown>; schema_version: number }) => void;
  onError?: (reason: string) => void;
}

const DEBOUNCE_MS = 500;

export function ChartLayoutSync({
  instrumentId,
  onConflict,
  onError,
}: ChartLayoutSyncProps): React.JSX.Element | null {
  const timeframe = useChartStore((s) => s.timeframe);
  const indicators = useChartStore((s) => s.indicators);
  const drawings = useChartStore((s) => s.drawings);
  const chartType = useChartStore((s) => s.chartType);

  const etagRef = useRef<string | null>(null);
  const pendingRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inflightRef = useRef<AbortController | null>(null);
  const initRef = useRef(false);

  useEffect(() => {
    if (instrumentId === null) return;
    if (!initRef.current) {
      initRef.current = true;
      return;
    }

    if (pendingRef.current !== null) clearTimeout(pendingRef.current);
    pendingRef.current = setTimeout(() => {
      pendingRef.current = null;
      const payload: LayoutPayload = { timeframe, indicators, drawings, chartType };
      inflightRef.current?.abort();
      inflightRef.current = new AbortController();
      void pushLayout(instrumentId, payload, etagRef.current, inflightRef.current.signal).then(
        (result) => handleResult(result, onConflict, onError, etagRef),
      );
    }, DEBOUNCE_MS);
  }, [instrumentId, timeframe, indicators, drawings, chartType, onConflict, onError]);

  useEffect(() => () => {
    if (pendingRef.current !== null) clearTimeout(pendingRef.current);
    inflightRef.current?.abort();
  }, []);

  return null;
}

function handleResult(
  result: LayoutSyncResult,
  onConflict: ChartLayoutSyncProps['onConflict'],
  onError: ChartLayoutSyncProps['onError'],
  etagRef: { current: string | null },
): void {
  if (result.kind === 'ok') {
    etagRef.current = result.etag;
  } else if (result.kind === 'conflict') {
    etagRef.current = result.remote.updated_at;
    onConflict?.(result.remote);
  } else {
    if (result.reason === 'aborted') return;
    onError?.(result.reason);
  }
}
