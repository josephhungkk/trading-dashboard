import * as React from 'react';
import { useEffect, useRef } from 'react';
import { init, dispose } from 'klinecharts';
import type { Chart, KLineData } from 'klinecharts';
import { useChartStore } from './stores/chartStore';
import { useLiveTailStore, FINAL_REVISION_VAL } from './stores/liveTailStore';
import { fetchBars, toChartBars } from './services/bars';
import { openLiveTail } from './services/liveTail';
import type { BarEnvelope } from './services/liveTail';

interface TradeChartProps {
  canonicalId: string;
}

export function TradeChart({ canonicalId }: TradeChartProps): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);
  // Holds the live subscribeBar callback so the DataLoader can push ticks.
  const barCallbackRef = useRef<((data: KLineData) => void) | null>(null);
  const timeframe = useChartStore((s) => s.timeframe);
  const indicators = useChartStore((s) => s.indicators);
  const liveTail = useLiveTailStore();

  // Mount + initial data load via v10 DataLoader API; re-run on canonicalId/timeframe change.
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = init(containerRef.current);
    if (!chart) return;
    chartRef.current = chart;

    // v10: symbol + period must be set before setDataLoader triggers getBars.
    chart.setSymbol({ ticker: canonicalId, pricePrecision: 2, volumePrecision: 0 });
    // Parse timeframe string (e.g. "1m", "5m", "1h", "1d") into v10 Period.
    chart.setPeriod(parseTimeframe(timeframe));

    chart.setDataLoader({
      getBars: ({ callback }) => {
        const end = new Date();
        const start = new Date(end.getTime() - 30 * 24 * 60 * 60 * 1000); // 30d
        fetchBars({ canonicalId, timeframe, start, end, limit: 10000 })
          .then((page) => {
            callback(toChartBars(page.bars), false);
          })
          .catch((err: unknown) => {
            console.warn('[TradeChart] bars fetch failed', err);
            callback([], false);
          });
      },
      subscribeBar: ({ callback }) => {
        barCallbackRef.current = callback;
      },
      unsubscribeBar: () => {
        barCallbackRef.current = null;
      },
    });

    const container = containerRef.current;
    return () => {
      barCallbackRef.current = null;
      dispose(container);
      chartRef.current = null;
    };
  }, [canonicalId, timeframe]);

  // Sync indicators into chart whenever the list changes.
  useEffect(() => {
    if (!chartRef.current) return;
    // TODO(Task 37): also remove indicators no longer in the array.
    for (const name of indicators) {
      chartRef.current.createIndicator(name, false, { id: 'candle_pane' });
    }
  }, [indicators]);

  // Live-tail WS subscription — feeds ticks via barCallbackRef into the DataLoader.
  useEffect(() => {
    const jwt = readJwtFromCookie();
    if (!jwt) return;

    const handle = openLiveTail(canonicalId, timeframe, jwt, (env: BarEnvelope) => {
      if (!liveTail.shouldAccept(canonicalId, timeframe, env.bucket_start, env.revision)) {
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

      liveTail.recordSeen(canonicalId, timeframe, env.bucket_start, env.revision);

      if (!env.partial && env.revision === FINAL_REVISION_VAL) {
        liveTail.lockBucket(canonicalId, timeframe, env.bucket_start);
      }
    });

    return () => {
      handle.close();
    };
  }, [canonicalId, timeframe, liveTail]);

  return (
    <div
      ref={containerRef}
      className="h-full w-full"
      data-testid="trade-chart"
    />
  );
}

/**
 * Parses a timeframe string like "1m", "5m", "1h", "1d" into a klinecharts v10 Period.
 * Defaults to 1-minute if the format is unrecognised.
 */
function parseTimeframe(tf: string): import('klinecharts').Period {
  const match = /^(\d+)([smhd])$/.exec(tf);
  if (!match) return { type: 'minute', span: 1 };
  const span = parseInt(match[1] ?? '1', 10);
  switch (match[2]) {
    case 's': return { type: 'second', span };
    case 'm': return { type: 'minute', span };
    case 'h': return { type: 'hour', span };
    case 'd': return { type: 'day', span };
    default:  return { type: 'minute', span: 1 };
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
