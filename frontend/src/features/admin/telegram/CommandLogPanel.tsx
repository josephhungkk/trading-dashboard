import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { adminFetch } from '@/services/admin/api';

interface CommandLogEntry {
  id: number;
  ts: string;
  chat_id: number;
  from_user_id: number;
  command: string;
  args: string | null;
  outcome: 'ok' | 'rate_limited' | 'unauthorized' | 'error';
  duration_ms: number | null;
}

function outcomeClass(outcome: CommandLogEntry['outcome']): string {
  switch (outcome) {
    case 'ok':
      return 'text-positive';
    case 'rate_limited':
      return 'text-warning';
    case 'unauthorized':
      return 'text-warning';
    case 'error':
      return 'text-negative';
    default:
      return 'text-fg-muted';
  }
}

export function CommandLogPanel(): React.JSX.Element {
  const { data, isError } = useQuery<CommandLogEntry[]>({
    queryKey: ['telegram-command-log'],
    queryFn: () => adminFetch<CommandLogEntry[]>('/api/admin/telegram/command-log'),
    refetchInterval: 30_000,
  });

  return (
    <div className="grid gap-3 rounded-md border border-border bg-panel p-4">
      <h3 className="text-sm font-semibold text-fg">Command Log</h3>
      {isError && <p className="text-xs text-negative">Failed to load command log.</p>}
      {!data || data.length === 0 ? (
        <p className="text-xs text-fg-muted">No commands logged.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-fg-muted">
                <th className="pb-1 pr-3">Time</th>
                <th className="pb-1 pr-3">Chat</th>
                <th className="pb-1 pr-3">Command</th>
                <th className="pb-1 pr-3">Args</th>
                <th className="pb-1 pr-3">Outcome</th>
                <th className="pb-1">ms</th>
              </tr>
            </thead>
            <tbody>
              {data.map(entry => (
                <tr key={entry.id} className="border-t border-border">
                  <td className="py-1 pr-3 font-mono text-fg-muted">
                    {new Date(entry.ts).toLocaleTimeString()}
                  </td>
                  <td className="py-1 pr-3 font-mono">{entry.chat_id}</td>
                  <td className="py-1 pr-3 font-mono">{entry.command}</td>
                  <td className="py-1 pr-3 text-fg-muted">{entry.args ?? '—'}</td>
                  <td className={`py-1 pr-3 ${outcomeClass(entry.outcome)}`}>{entry.outcome}</td>
                  <td className="py-1 font-mono text-fg-muted">
                    {entry.duration_ms != null ? entry.duration_ms : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
