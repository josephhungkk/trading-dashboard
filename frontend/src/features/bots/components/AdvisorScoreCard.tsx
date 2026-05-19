import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getAdvisorAttribution } from '@/services/advisor/api';
import type { AttributionSummary } from '@/services/advisor/types';

interface AdvisorScoreCardProps {
  botId: string;
  advisorMode: 'OFF' | 'OBSERVE' | 'VETO';
}

const WINDOWS = ['15m', '1h', '4h', 'eod'] as const;

function AccuracyBar({ value }: { value: number | null }) {
  if (value === null) return <span className="text-sm text-muted-foreground">—</span>;
  const pct = Math.round(value * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-green-500" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-sm tabular-nums">{pct}%</span>
    </div>
  );
}

export function AdvisorScoreCard({ botId, advisorMode }: AdvisorScoreCardProps) {
  const [selectedWindow, setSelectedWindow] = useState<string>('1h');

  const { data } = useQuery<AttributionSummary>({
    queryKey: ['advisor-attribution', botId, selectedWindow],
    queryFn: () => getAdvisorAttribution(botId, selectedWindow),
    staleTime: 300_000,
    enabled: advisorMode !== 'OFF',
  });

  if (advisorMode === 'OFF') return null;

  if (!data || data.complete_count === 0) {
    return (
      <div className="rounded-lg border p-4 text-sm text-muted-foreground">
        No attribution data yet — outcomes computed after window elapses.
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <div className="flex items-center justify-between">
        <h3 className="font-medium">Advisor Accuracy</h3>
        <select
          className="rounded border px-2 py-1 text-sm"
          value={selectedWindow}
          onChange={(e) => setSelectedWindow(e.target.value)}
          aria-label="Attribution window"
        >
          {WINDOWS.map((w) => (
            <option key={w} value={w}>{w.toUpperCase()}</option>
          ))}
        </select>
      </div>

      <div className="space-y-2">
        <div>
          <div className="mb-1 text-xs text-muted-foreground">Veto accuracy</div>
          <AccuracyBar value={data.veto_accuracy} />
        </div>
        <div>
          <div className="mb-1 text-xs text-muted-foreground">Approve accuracy</div>
          <AccuracyBar value={data.approve_accuracy} />
        </div>
      </div>

      {data.avg_avoided_loss_quote !== null && (
        <div className="text-sm text-green-600">
          Avg avoided loss: {Number(data.avg_avoided_loss_quote).toFixed(2)}{' '}
          <span className="text-xs text-muted-foreground">(quote currency)</span>
        </div>
      )}
      {data.avg_missed_gain_quote !== null && (
        <div className="text-sm text-red-500">
          Avg missed gain: {Number(data.avg_missed_gain_quote).toFixed(2)}{' '}
          <span className="text-xs text-muted-foreground">(quote currency)</span>
        </div>
      )}

      <div className="border-t pt-2 text-xs text-muted-foreground">
        {data.complete_count} complete · {data.pending_count} pending ·{' '}
        {data.bars_unavailable_count} unavailable · Updated{' '}
        {new Date(data.generated_at).toLocaleTimeString()}
      </div>
    </div>
  );
}
