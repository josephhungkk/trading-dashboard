import * as React from 'react';
import { getRouteApi, Link } from '@tanstack/react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

const routeApi = getRouteApi('/bots/$botId');
import { getBot, updateBot } from '../../services/bots/api';
import { useBotStatus } from './hooks/useBotStatus';
import { BotControlBar } from './components/BotControlBar';
import { ParamsEditor } from './components/ParamsEditor';
import { RiskCapsForm } from './components/RiskCapsForm';
import { BotRunsTable } from './components/BotRunsTable';
import { BotOrdersTable } from './components/BotOrdersTable';
import { AdvisorConfigForm } from './components/AdvisorConfigForm';
import { AdvisorDecisionsTable } from './components/AdvisorDecisionsTable';
import { AccountAdvisorConfigForm } from './components/AccountAdvisorConfigForm';

type Tab = 'overview' | 'runs' | 'orders' | 'risk' | 'advisor';

interface BotAccountAdvisorConfig {
  account_id: string;
  advisor_config_override: Record<string, unknown> | null;
}

interface BotWithAdvisorAccounts {
  accounts?: BotAccountAdvisorConfig[];
  bot_accounts?: BotAccountAdvisorConfig[];
  account_ids?: string[];
  account_advisor_config_overrides?: Record<string, Record<string, unknown> | null>;
  advisor_config?: Record<string, unknown> | null;
}

export function BotDetailPage(): React.JSX.Element {
  useBotStatus();
  const { botId } = routeApi.useParams();
  const qc = useQueryClient();
  const [tab, setTab] = React.useState<Tab>('overview');
  const [editParams, setEditParams] = React.useState(false);
  const [pendingParams, setPendingParams] = React.useState<Record<string, unknown> | null>(null);

  const { data: bot, isLoading, error, refetch: refetchBot } = useQuery({
    queryKey: ['bot', botId],
    queryFn: () => getBot(botId),
  });

  const updateMut = useMutation({
    mutationFn: (params: Record<string, unknown>) =>
      updateBot(botId, { params_json: params }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['bot', botId] });
      setEditParams(false);
      setPendingParams(null);
    },
  });

  if (isLoading) return <p className="p-4 text-sm text-muted-foreground">Loading…</p>;
  if (error != null || bot == null)
    return <p className="p-4 text-sm text-destructive">Bot not found.</p>;

  const tabs: { id: Tab; label: string }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'runs', label: 'Runs' },
    { id: 'orders', label: 'Orders' },
    { id: 'risk', label: 'Risk caps' },
    { id: 'advisor', label: 'Advisor' },
  ];
  const botAdvisorData = bot as typeof bot & BotWithAdvisorAccounts;
  const botAccounts =
    botAdvisorData.accounts ??
    botAdvisorData.bot_accounts ??
    botAdvisorData.account_ids?.map((accountId) => ({
      account_id: accountId,
      advisor_config_override:
        botAdvisorData.account_advisor_config_overrides?.[accountId] ?? null,
    })) ??
    [];
  const botConfig = botAdvisorData.advisor_config ?? {};

  return (
    <main className="p-4">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">{bot.name}</h1>
          <p className="text-sm text-muted-foreground">
            {bot.strategy_file} · v{bot.version} · {bot.mode} · {bot.bar_timeframe}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to="/bots/$botId/backtest"
            params={{ botId }}
            className="text-sm underline"
          >
            Run Backtest
          </Link>
          <BotControlBar bot={bot} />
        </div>
      </div>

      {bot.error_msg != null && (
        <p className="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-destructive" role="alert">
          {bot.error_msg}
        </p>
      )}

      <div className="mb-4 flex gap-1 border-b">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm font-medium ${
              tab === t.id
                ? 'border-b-2 border-primary text-primary'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="space-y-4">
          <div>
            <div className="mb-1 flex items-center justify-between">
              <h2 className="text-sm font-semibold">Strategy params</h2>
              {!editParams && (
                <button
                  onClick={() => {
                    setEditParams(true);
                    setPendingParams(bot.params_json);
                  }}
                  className="text-xs underline"
                  disabled={bot.status !== 'stopped'}
                >
                  Edit
                </button>
              )}
            </div>
            {editParams && pendingParams != null ? (
              <div className="space-y-2">
                <ParamsEditor
                  value={pendingParams}
                  schema={bot.params_schema_json}
                  onChange={setPendingParams}
                />
                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      setEditParams(false);
                      setPendingParams(null);
                    }}
                    className="btn-secondary text-xs"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => updateMut.mutate(pendingParams)}
                    disabled={updateMut.isPending}
                    className="btn-primary text-xs"
                  >
                    {updateMut.isPending ? 'Saving…' : 'Save params'}
                  </button>
                </div>
                {updateMut.isError && (
                  <p className="text-xs text-destructive">
                    {(updateMut.error as Error).message}
                  </p>
                )}
              </div>
            ) : (
              <pre className="overflow-x-auto rounded bg-muted px-3 py-2 text-xs">
                {JSON.stringify(bot.params_json, null, 2)}
              </pre>
            )}
          </div>
          <div className="text-xs text-muted-foreground">
            Created {new Date(bot.created_at).toLocaleString()} · Updated{' '}
            {new Date(bot.updated_at).toLocaleString()}
          </div>
        </div>
      )}

      {tab === 'runs' && <BotRunsTable botId={botId} />}
      {tab === 'orders' && <BotOrdersTable botId={botId} />}
      {tab === 'risk' && <RiskCapsForm botId={botId} />}
      {tab === 'advisor' && (
        <div className="space-y-6">
          <AdvisorConfigForm botId={botId} />
          <AdvisorDecisionsTable botId={botId} isAdmin />
          {botAccounts.map((account) => (
            <section key={account.account_id} className="space-y-2">
              <h4 className="text-sm font-semibold">
                Account {account.account_id} advisor override
              </h4>
              <AccountAdvisorConfigForm
                botId={bot.id}
                account={account}
                botConfig={botConfig}
                onSaved={() => {
                  void refetchBot();
                }}
              />
            </section>
          ))}
        </div>
      )}
    </main>
  );
}
