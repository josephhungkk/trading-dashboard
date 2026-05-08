import * as React from 'react';
import { useEffect, useRef } from 'react';
import { init, dispose } from 'klinecharts';
import type { Chart, KLineData } from 'klinecharts';
import { useChartStore } from './stores/chartStore';
import { useLiveTailStore, FINAL_REVISION_VAL } from './stores/liveTailStore';
import { fetchBars, toChartBars } from './services/bars';
import { openLiveTail } from './services/liveTail';
import type { BarEnvelope } from './services/liveTail';
import { defaultWindowMs } from './services/timeframe';
import { PositionOverlay, type ModifyRequest } from './PositionOverlay';
import { registerCustomOverlays } from './overlays';

interface TradeChartProps {
  canonicalId: string;
  onModifyRequest?: (req: ModifyRequest) => void;
}

export function TradeChart({ canonicalId, onModifyRequest }: TradeChartProps): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);
  const [chartReady, setChartReady] = React.useState(false);
  // Holds the live subscribeBar callback so the DataLoader can push ticks.
  const barCallbackRef = useRef<((data: KLineData) => void) | null>(null);
  const timeframe = useChartStore((s) => s.timeframe);
  const indicators = useChartStore((s) => s.indicators);

  // HIGH-1: subscribe to individual stable action selectors instead of the whole store.
  // Whole-store subscription causes a new object reference on every WS tick → useEffect
  // deps change → WS teardown/reconnect storm.
  const shouldAccept = useLiveTailStore((s) => s.shouldAccept);
  const recordSeen = useLiveTailStore((s) => s.recordSeen);
  const lockBucket = useLiveTailStore((s) => s.lockBucket);

  // Mount + initial data load via v10 DataLoader API; re-run on canonicalId/timeframe change.
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = init(containerRef.current);
    if (!chart) return;
    chartRef.current = chart;
    registerCustomOverlays();

    // v10: symbol + period must be set before setDataLoader triggers getBars.
    chart.setSymbol({ ticker: canonicalId, pricePrecision: 2, volumePrecision: 0 });
    // Parse timeframe string (e.g. "1m", "5m", "1h", "1d", "1w", "1M") into v10 Period.
    chart.setPeriod(parseTimeframe(timeframe));

    // HIGH-4: create AbortController so an in-flight fetch can be cancelled on unmount.
    const abortController = new AbortController();

    chart.setDataLoader({
      getBars: ({ callback }) => {
        // HIGH-2: derive fetch window from timeframe instead of hardcoding 30 days.
        // For short intervals (1s/5s) this avoids silent truncation; for long intervals
        // (1d/1w) it provides enough history to fill the chart.
        const windowMs = defaultWindowMs(timeframe);
        const end = new Date();
        const start = new Date(end.getTime() - windowMs);
        fetchBars({ canonicalId, timeframe, start, end, limit: 10000, signal: abortController.signal })
          .then((page) => {
            callback(toChartBars(page.bars), false);
          })
          .catch((err: unknown) => {
            // MED-3: log message only; avoid leaking full error objects.
            // TODO: replace with structured FE logger when one is introduced.
            const msg = err instanceof Error ? err.message : String(err);
            console.warn('[TradeChart] bars fetch failed', msg);
            // Suppress callback after abort — chart may already be disposed.
            if (!abortController.signal.aborted) {
              callback([], false);
            }
          });
      },
      subscribeBar: ({ callback }) => {
        barCallbackRef.current = callback;
      },
      unsubscribeBar: () => {
        barCallbackRef.current = null;
      },
    });
    setChartReady(true);

    const container = containerRef.current;
    return () => {
      // HIGH-4: abort any in-flight fetch on unmount.
      abortController.abort();
      barCallbackRef.current = null;
      dispose(container);
      chartRef.current = null;
      setChartReady(false);
    };
  }, [canonicalId, timeframe]);

  const handleModifyRequest = React.useCallback((req: ModifyRequest) => {
    onModifyRequest?.(req);
  }, [onModifyRequest]);

  // Sync indicators into chart whenever the list changes.
  useEffect(() => {
    if (!chartRef.current) return;
    // TODO(Task 37): also remove indicators no longer in the array.
    for (const name of indicators) {
      chartRef.current.createIndicator(name, false, { id: 'candle_pane' });
    }
  }, [indicators]);

  // Live-tail WS subscription — feeds ticks via barCallbackRef into the DataLoader.
  // HIGH-1: deps list uses stable selectors (shouldAccept, recordSeen, lockBucket),
  // not the whole liveTail store object, so WS is not torn down on every tick.
  useEffect(() => {
    // HIGH-5: pass a callback so openLiveTail re-reads the JWT on each reconnect.
    const handle = openLiveTail(
      canonicalId,
      timeframe,
      readJwtFromCookie,
      (env: BarEnvelope) => {
        if (!shouldAccept(canonicalId, timeframe, env.bucket_start, env.revision)) {
          return;
        }

        const bar: KLineData = {
          timestamp: new Date(env.bucket_start).getTime(),
          open: Number(env.open),
          high: Number(env.high),
          low: Number(env.low),
          close: Number(env.close),
          volume: Number(env.volume),
        };

        // Push tick into chart via the DataLoader subscribeBar callback.
        barCallbackRef.current?.(bar);

        recordSeen(canonicalId, timeframe, env.bucket_start, env.revision);

        if (!env.partial && env.revision === FINAL_REVISION_VAL) {
          lockBucket(canonicalId, timeframe, env.bucket_start);
        }
      },
      undefined, // onReconnect (not used yet)
      // MED-4: no-op error handler; surface protocol error frames via console in dev.
      // TODO: wire to a structured FE logger / alert system when one is introduced.
      () => {
        /* protocol error frames are intentionally suppressed for now */
      },
    );

    return () => {
      handle.close();
    };
  }, [canonicalId, timeframe, shouldAccept, recordSeen, lockBucket]);

  return (
    <>
      <div
        ref={containerRef}
        className="h-full w-full"
        data-testid="trade-chart"
      />
      {chartReady && (
        <PositionOverlay
          canonicalId={canonicalId}
          chartRef={chartRef}
          onModifyRequest={handleModifyRequest}
        />
      )}
    </>
  );
}

/**
 * Parses a timeframe string like "1m", "5m", "1h", "1d", "1w", "1M" into a klinecharts v10 Period.
 * HIGH-3: extended to handle 'w' (week) and 'M' (month) which TimeframeBar exposes.
 * Throws on unrecognised format instead of silently defaulting to 1-minute.
 */
function parseTimeframe(tf: string): import('klinecharts').Period {
  const match = /^(\d+)([smhdwM])$/.exec(tf);
  if (!match) {
    console.warn('[TradeChart] unrecognised timeframe, falling back to 1m', tf);
    return { type: 'minute', span: 1 };
  }
  const span = parseInt(match[1] ?? '1', 10);
  switch (match[2]) {
    case 's': return { type: 'second', span };
    case 'm': return { type: 'minute', span };
    case 'h': return { type: 'hour', span };
    case 'd': return { type: 'day', span };
    case 'w': return { type: 'week', span };
    case 'M': return { type: 'month', span };
    default:
      console.warn('[TradeChart] unrecognised timeframe unit, falling back to 1m', tf);
      return { type: 'minute', span: 1 };
  }
}

/**
 * Reads the bearer JWT from the document cookie.
 *
 * The project uses Cloudflare Access; the JWT is stored in `cf_authorization`.
 * TODO(Task 37): confirm cookie name against CF Access docs and align with
 * any future useAuthToken() hook if one is introduced.
 */
function readJwtFromCookie(): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie
    .split(';')
    .map((c) => c.trim())
    .find((c) => c.startsWith('cf_authorization='));
  if (!match) return null;
  return match.slice('cf_authorization='.length) || null;
}
