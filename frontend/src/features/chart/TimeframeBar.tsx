import * as React from 'react';
import { useState } from 'react';
import { useChartStore } from './stores/chartStore';
import { cn } from '@/lib/utils';

// Range presets — display-only for now; fetch-range wiring deferred.
// TODO(Task 36 follow-up): clicking a range preset should update the bars fetch window.
const RANGES = ['1d', '5d', '1m', '3m', '6m', '1y', '5y', 'All', 'Custom'] as const;
type RangePreset = (typeof RANGES)[number];

// Interval options wired to chartStore.setTimeframe.
const INTERVALS = [
  '1s', '5s', '10s', '15s', '30s', '45s',
  '1m', '5m', '15m', '30m',
  '1h', '1d', '1w', '1M',
] as const;
type Interval = (typeof INTERVALS)[number];

/**
 * Bottom dual-pill bar.
 * - Top row: range presets (display-only, TODO wiring).
 * - Bottom row: interval buttons wired to chartStore.setTimeframe.
 *
 * Mobile (<md): only the interval row is shown; ranges collapse to hidden.
 */
export function TimeframeBar(): React.JSX.Element {
  const timeframe = useChartStore((s) => s.timeframe);
  const setTimeframe = useChartStore((s) => s.setTimeframe);
  const [rangeOpen, setRangeOpen] = useState(false);

  return (
    <div
      className="relative flex flex-col gap-0.5 border-t border-border px-2 py-1"
      role="group"
      aria-label="Timeframe controls"
    >
      {/* Range presets — hidden below md */}
      <div
        className="hidden flex-wrap gap-1 text-xs md:flex"
        role="group"
        aria-label="Range presets"
        data-testid="timeframe-range-row"
      >
        {RANGES.map((r: RangePreset) => (
          <button
            key={r}
            type="button"
            aria-label={`Range ${r}`}
            // TODO(Task 36 follow-up): call setFetchRange(r) when range wiring is implemented.
            // Marked aria-disabled + tabIndex=-1 until wiring lands.
            // MED-8: no onClick needed; disabled + aria-disabled are sufficient.
            aria-disabled="true"
            tabIndex={-1}
            className={cn(
              'min-h-[2.75rem] min-w-[2.75rem] rounded px-2 text-xs',
              'text-fg-muted hover:bg-muted/10 hover:text-fg',
              'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-active',
              'cursor-not-allowed opacity-60',
            )}
          >
            {r}
          </button>
        ))}
      </div>

      {/* Interval row — always visible */}
      <div
        className="flex flex-wrap gap-0.5 text-xs"
        role="group"
        aria-label="Interval"
        data-testid="timeframe-interval-row"
      >
        {INTERVALS.map((tf: Interval) => {
          const active = tf === timeframe;
          return (
            <button
              key={tf}
              type="button"
              aria-label={`Interval ${tf}`}
              aria-pressed={active}
              onClick={() => setTimeframe(tf)}
              className={cn(
                'min-h-[2.75rem] min-w-[2.75rem] rounded px-2 text-xs font-medium',
                'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-active',
                active
                  ? 'bg-accent-active text-primary-fg'
                  : 'text-fg-muted hover:bg-muted/10 hover:text-fg',
              )}
            >
              {tf}
            </button>
          );
        })}
        <button
          type="button"
          className={cn(
            'min-h-[2.75rem] min-w-[2.75rem] rounded px-2 text-xs font-medium md:hidden',
            'text-fg-muted hover:bg-muted/10 hover:text-fg',
            'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-active',
          )}
          aria-label="Range"
          onClick={() => setRangeOpen(true)}
        >
          Range
        </button>
      </div>

      {rangeOpen ? (
        <div
          className="absolute inset-x-0 bottom-full z-20 border-t border-border bg-background p-2 shadow-lg md:hidden"
          role="dialog"
          aria-modal="true"
          aria-label="Range presets menu"
        >
          <div className="flex flex-wrap gap-1 text-xs">
            {RANGES.map((r: RangePreset) => (
              <button
                key={r}
                type="button"
                aria-label={`Mobile range ${r}`}
                // MED-8: no onClick needed; aria-disabled + tabIndex=-1 are sufficient.
                aria-disabled="true"
                tabIndex={-1}
                className={cn(
                  'min-h-[2.75rem] min-w-[2.75rem] rounded px-2 text-xs',
                  'text-fg-muted hover:bg-muted/10 hover:text-fg',
                  'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-active',
                  'cursor-not-allowed opacity-60',
                )}
              >
                {r}
              </button>
            ))}
            <button
              type="button"
              className={cn(
                'min-h-[2.75rem] min-w-[2.75rem] rounded px-2 text-xs font-medium',
                'text-fg-muted hover:bg-muted/10 hover:text-fg',
                'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-active',
              )}
              aria-label="Close range presets"
              onClick={() => setRangeOpen(false)}
            >
              Close
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
