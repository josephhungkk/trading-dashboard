import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { listRuns } from '../../../services/bots/api';

interface Props {
  botId: string;
}

const stopReasonLabel: Record<string, string> = {
  manual: 'Manual',
  error: 'Error',
  daily_loss_cap: 'Daily loss cap',
  kill_switch: 'Kill switch',
};

export function BotRunsTable({ botId }: Props): React.JSX.Element {
  const { data, isLoading } = useQuery({
    queryKey: ['bot-runs', botId],
    queryFn: () => listRuns(botId),
  });

  if (isLoading) return <p className="text-sm text-muted-foreground">Loading runs…</p>;

  const items = data?.items ?? [];

  if (items.length === 0)
    return <p className="text-sm text-muted-foreground">No runs yet.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-muted-foreground">
            <th className="pb-2 pr-4">Version</th>
            <th className="pb-2 pr-4">Started</th>
            <th className="pb-2 pr-4">Stopped</th>
            <th className="pb-2">Stop reason</th>
          </tr>
        </thead>
        <tbody>
          {items.map((run) => (
            <tr key={run.id} className="border-b last:border-0">
              <td className="py-2 pr-4 font-mono">{run.version}</td>
              <td className="py-2 pr-4 font-mono text-xs">
                {new Date(run.started_at).toLocaleString()}
              </td>
              <td className="py-2 pr-4 font-mono text-xs">
                {run.stopped_at != null ? new Date(run.stopped_at).toLocaleString() : '—'}
              </td>
              <td className="py-2">
                {run.stop_reason != null ? (
                  <span
                    className={
                      run.stop_reason === 'error' || run.stop_reason === 'kill_switch'
                        ? 'text-destructive'
                        : 'text-muted-foreground'
                    }
                  >
                    {stopReasonLabel[run.stop_reason] ?? run.stop_reason}
                  </span>
                ) : (
                  <span className="text-green-700">Running</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
