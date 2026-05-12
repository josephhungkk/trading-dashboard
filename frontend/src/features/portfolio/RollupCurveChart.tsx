import * as React from 'react';

import type { CurveWindow, RollupCurve } from '@/services/portfolio/types';

interface Props {
  data: RollupCurve | undefined;
  window: CurveWindow;
  onWindowChange: (w: CurveWindow) => void;
  loading: boolean;
}

const WINDOWS: CurveWindow[] = ['intraday', '30d', '1y'];

/**
 * Lightweight SVG sparkline. Avoids pulling klinecharts into the rollup
 * route — the bundle is already heavyweight on the chart route, and the
 * portfolio curve only needs a single area path with axes.
 */
export function RollupCurveChart({
  data,
  window,
  onWindowChange,
  loading,
}: Props): React.JSX.Element {
  return (
    <section
      className="rounded-md border border-border bg-panel p-4"
      data-testid="rollup-curve-chart"
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          NLV trajectory
        </h2>
        <div role="tablist" className="flex gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w}
              role="tab"
              type="button"
              aria-selected={window === w}
              onClick={() => onWindowChange(w)}
              className={`rounded-md px-2 py-1 text-xs ${
                window === w
                  ? 'bg-blue-600 text-white'
                  : 'bg-transparent text-muted-foreground hover:bg-muted'
              }`}
              data-testid={`rollup-curve-window-${w}`}
            >
              {w}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
          Loading…
        </div>
      ) : !data || data.totals.length === 0 ? (
        <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
          No data for this window
        </div>
      ) : (
        <Sparkline data={data} />
      )}
    </section>
  );
}

function Sparkline({ data }: { data: RollupCurve }): React.JSX.Element {
  const totals = data.totals;
  const values = totals.map((p) => Number(p.total_nlv_base));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const width = 800;
  const height = 120;
  const path = values
    .map((v: number, i: number) => {
      const x = (i / Math.max(values.length - 1, 1)) * width;
      const y = height - ((v - min) / range) * height;
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="h-32 w-full"
      role="img"
      aria-label={`NLV curve, ${totals.length} points`}
    >
      <path
        d={path}
        fill="none"
        stroke="rgb(37, 99, 235)"
        strokeWidth="2"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
