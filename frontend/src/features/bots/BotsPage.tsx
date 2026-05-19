import * as React from 'react';
import { Link } from '@tanstack/react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listBots, deleteBot } from '../../services/bots/api';
import { useBotStatus } from './hooks/useBotStatus';
import { BotControlBar } from './components/BotControlBar';
import type { BotStatus } from '../../services/bots/types';

const statusBadge: Record<BotStatus, string> = {
  stopped: 'bg-muted text-muted-foreground',
  starting: 'bg-yellow-100 text-yellow-800',
  running: 'bg-green-100 text-green-800',
  pausing: 'bg-yellow-100 text-yellow-800',
  paused: 'bg-blue-100 text-blue-800',
  error: 'bg-red-100 text-red-800',
};

export function BotsPage(): React.JSX.Element {
  useBotStatus();
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = React.useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['bots', statusFilter || undefined],
    queryFn: () => listBots(statusFilter ? { status: statusFilter } : undefined),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteBot(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['bots'] }),
  });

  const bots = data?.items ?? [];

  return (
    <main className="p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Bots</h1>
        <div className="flex items-center gap-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded border border-border bg-background px-3 py-2 text-sm"
          >
            <option value="">All statuses</option>
            <option value="running">Running</option>
            <option value="paused">Paused</option>
            <option value="stopped">Stopped</option>
            <option value="error">Error</option>
          </select>
          <Link to="/bots/new" className="btn-primary text-sm">
            New bot
          </Link>
        </div>
      </div>

      {isLoading && <p className="text-sm text-muted-foreground">Loading bots…</p>}

      {!isLoading && bots.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No bots found.{' '}
          <Link to="/bots/new" className="underline">
            Create one
          </Link>
          .
        </p>
      )}

      <div className="space-y-3">
        {bots.map((bot) => (
          <div key={bot.id} className="rounded border border-border p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <Link
                  to="/bots/$botId"
                  params={{ botId: bot.id }}
                  className="font-semibold hover:underline"
                >
                  {bot.name}
                </Link>
                <p className="text-xs text-muted-foreground">{bot.strategy_file}</p>
              </div>
              <div className="flex items-center gap-2">
                <span
                  className={`rounded px-2 py-0.5 text-xs font-medium ${statusBadge[bot.status]}`}
                >
                  {bot.status}
                </span>
                <span className="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                  {bot.mode}
                </span>
              </div>
            </div>

            {bot.error_msg != null && (
              <p className="mt-1 text-xs text-destructive" role="alert">
                {bot.error_msg}
              </p>
            )}

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <BotControlBar bot={bot} />
              <button
                onClick={() => {
                  if (confirm(`Delete bot "${bot.name}"?`)) deleteMut.mutate(bot.id);
                }}
                className="btn-destructive ml-auto text-xs"
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>
    </main>
  );
}
