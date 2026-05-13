import * as React from 'react';

import type { DryRunResult, DryRunSampleFire } from '@/services/alerts/useDryRun';

interface Props {
  result: DryRunResult | null;
  isPending: boolean;
  insufficientAcknowledged: boolean;
  onAcknowledge: (next: boolean) => void;
  onReRun: () => void;
}

function ResolutionBanner({
  resolution,
}: {
  resolution: DryRunResult['replay_resolution'];
}): React.JSX.Element {
  const tone =
    resolution === 'insufficient'
      ? 'border-amber-300 bg-amber-50 text-amber-900'
      : 'border-border bg-muted text-foreground';
  return (
    <div
      className={`rounded-md border px-3 py-2 text-xs ${tone}`}
      data-testid={`dry-run-resolution-${resolution}`}
    >
      Resolution: <span className="font-mono">{resolution}</span>
    </div>
  );
}

export function DryRunPanel({
  result,
  isPending,
  insufficientAcknowledged,
  onAcknowledge,
  onReRun,
}: Props): React.JSX.Element {
  return (
    <section
      className="flex flex-col gap-3 rounded-md border border-border bg-panel p-4"
      data-testid="dry-run-panel"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Dry-run replay
        </h2>
        <button
          type="button"
          onClick={onReRun}
          disabled={isPending}
          className="rounded-md border border-border px-3 py-1 text-xs hover:bg-muted disabled:opacity-50"
          data-testid="dry-run-rerun"
        >
          {isPending ? 'Replaying…' : 'Re-run'}
        </button>
      </div>
      {result === null ? (
        <p className="text-sm text-muted-foreground">No replay yet.</p>
      ) : (
        <>
          <ResolutionBanner resolution={result.replay_resolution} />
          {result.replay_resolution === 'insufficient' ? (
            <label className="flex items-start gap-2 text-xs text-amber-900">
              <input
                type="checkbox"
                checked={insufficientAcknowledged}
                onChange={(e) => onAcknowledge(e.target.checked)}
                data-testid="dry-run-insufficient-ack"
              />
              I understand the backtest is unreliable for sub-minute windows.
            </label>
          ) : (
            <>
              <div className="text-sm">
                <span className="font-medium">{result.fire_count}</span> fire
                {result.fire_count === 1 ? '' : 's'} during replay window
                {result.truncated && (
                  <span className="ml-1 text-xs text-muted-foreground">
                    (showing first {result.sample_fires.length})
                  </span>
                )}
              </div>
              {result.sample_fires.length > 0 && (
                <ul className="space-y-1" data-testid="dry-run-samples">
                  {result.sample_fires.map((f: DryRunSampleFire, idx) => (
                    <li
                      key={idx}
                      className="flex justify-between rounded-md bg-muted/50 px-2 py-1 text-xs tabular-nums"
                    >
                      <span className="font-mono">{String(f.ts)}</span>
                      <span>{f.close !== undefined ? f.close.toFixed(2) : '—'}</span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
