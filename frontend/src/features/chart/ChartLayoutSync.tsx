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
  const initRef = useRef(false);

  // CRIT-2: ref-pin callbacks so they can be dropped from the debounce effect dep array.
  // Inline arrows passed from ChartPage re-create on every parent render, which would
  // reset the debounce window mid-flight and break coalescing (spec §6 Flow 7).
  const onConflictRef = useRef(onConflict);
  const onErrorRef = useRef(onError);
  useEffect(() => { onConflictRef.current = onConflict; }, [onConflict]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);

  // HIGH-1: replace inflightRef abort with a generation counter.
  // Aborting the in-flight PUT races with the server having already committed —
  // the aborted response never updates etagRef, so the next PUT uses a stale etag.
  // Generation counter lets us discard stale results without cancelling the request.
  const generationRef = useRef(0);

  // Unmount-only AbortController — aborts any pending network request on cleanup.
  const unmountAbortRef = useRef<AbortController | null>(null);

  // MED-2: track previous instrumentId so we can reset stale state on instrument change.
  const prevInstrumentIdRef = useRef<number | null>(null);

  // Mount/unmount effect — creates the single long-lived AbortController.
  useEffect(() => {
    unmountAbortRef.current = new AbortController();
    return () => {
      if (pendingRef.current !== null) clearTimeout(pendingRef.current);
      unmountAbortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (instrumentId === null) return;

    // MED-2: reset stale etag/generation/initRef when switching to a new instrument.
    if (prevInstrumentIdRef.current !== instrumentId) {
      etagRef.current = null;
      initRef.current = false;
      generationRef.current = 0;
      prevInstrumentIdRef.current = instrumentId;
    }

    if (!initRef.current) {
      initRef.current = true;
      return;
    }

    if (pendingRef.current !== null) clearTimeout(pendingRef.current);
    pendingRef.current = setTimeout(() => {
      pendingRef.current = null;
      // HIGH-1: bump generation before each PUT; discard results from older generations.
      const myGen = ++generationRef.current;
      const payload: LayoutPayload = { timeframe, indicators, drawings, chartType };
      pushLayout(instrumentId, payload, etagRef.current, unmountAbortRef.current?.signal)
        .then((result) => {
          // HIGH-1: discard stale results so a slower earlier PUT can't overwrite
          // the etag that a faster later PUT already set.
          if (myGen !== generationRef.current) return;
          handleResult(result, onConflictRef.current, onErrorRef.current, etagRef);
        })
        .catch((err: unknown) => {
          // HIGH-6: never let handler throws bubble as unhandled promise rejections.
          console.error('[ChartLayoutSync] sync handler failure', err); // noqa: no-console (intentional error log)
        });
    }, DEBOUNCE_MS);
    // CRIT-2: onConflict + onError intentionally omitted — ref-pinned above via onConflictRef/onErrorRef.
  }, [instrumentId, timeframe, indicators, drawings, chartType]);

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
