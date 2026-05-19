import * as React from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { createBot } from '../../services/bots/api';
import { StrategyFilePicker } from './components/StrategyFilePicker';
import { ParamsEditor } from './components/ParamsEditor';

export function BotCreatePage(): React.JSX.Element {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [name, setName] = React.useState('');
  const [strategyFile, setStrategyFile] = React.useState('');
  const [params, setParams] = React.useState<Record<string, unknown>>({});
  const [barTimeframe, setBarTimeframe] = React.useState('1m');
  const [mode, setMode] = React.useState<'paper' | 'live'>('paper');

  const mut = useMutation({
    mutationFn: () =>
      createBot({
        name,
        strategy_file: strategyFile,
        params_json: params,
        bar_timeframe: barTimeframe,
        mode,
        account_ids: [],
      }),
    onSuccess: (bot) => {
      void qc.invalidateQueries({ queryKey: ['bots'] });
      void navigate({ to: '/bots/$botId', params: { botId: bot.id } });
    },
  });

  const canSubmit = name.trim() !== '' && strategyFile !== '' && !mut.isPending;

  return (
    <main className="mx-auto max-w-lg p-4">
      <h1 className="mb-4 text-xl font-semibold">New bot</h1>

      <div className="space-y-4">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted-foreground">Name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My strategy"
            className="rounded border border-border bg-background px-3 py-2 text-sm"
          />
        </label>

        <div className="flex flex-col gap-1 text-sm">
          <label htmlFor="strategy-file" className="text-muted-foreground">
            Strategy file
          </label>
          <StrategyFilePicker id="strategy-file" value={strategyFile} onChange={setStrategyFile} />
        </div>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted-foreground">Bar timeframe</span>
          <select
            value={barTimeframe}
            onChange={(e) => setBarTimeframe(e.target.value)}
            className="rounded border border-border bg-background px-3 py-2 text-sm"
          >
            <option value="1m">1 minute</option>
            <option value="5m">5 minutes</option>
            <option value="15m">15 minutes</option>
            <option value="1h">1 hour</option>
            <option value="1d">1 day</option>
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted-foreground">Mode</span>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as 'paper' | 'live')}
            className="rounded border border-border bg-background px-3 py-2 text-sm"
          >
            <option value="paper">Paper</option>
            <option value="live">Live</option>
          </select>
        </label>

        <div className="flex flex-col gap-1 text-sm">
          <span className="text-muted-foreground">Strategy params (JSON)</span>
          <ParamsEditor value={params} schema={null} onChange={setParams} />
        </div>

        {mut.isError && (
          <p className="text-xs text-destructive" role="alert">
            {(mut.error as Error).message}
          </p>
        )}

        <div className="flex gap-2">
          <button onClick={() => void navigate({ to: '/bots' })} className="btn-secondary">
            Cancel
          </button>
          <button onClick={() => mut.mutate()} disabled={!canSubmit} className="btn-primary">
            {mut.isPending ? 'Creating…' : 'Create bot'}
          </button>
        </div>
      </div>
    </main>
  );
}
