import * as React from 'react';
import { Link, getRouteApi } from '@tanstack/react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getDigestLatest,
  getCorrelation,
  getExposureLimits,
  getGeneratedStrategies,
  approveStrategy,
  rejectStrategy,
} from '../../services/orchestrator/api';

const routeApi = getRouteApi('/orchestration');

type SortKey = 'sharpe_30d' | 'max_drawdown' | 'win_rate' | 'advisor_veto_accuracy_1h';
type SortDir = 'asc' | 'desc';

function SortHeader({
  label,
  k,
  sortKey,
  sortDir,
  onSort,
}: {
  label: string;
  k: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (k: SortKey) => void;
}) {
  const active = sortKey === k;
  return (
    <th
      className="cursor-pointer select-none px-3 py-2 text-left text-xs font-medium uppercase text-muted-foreground hover:text-foreground"
      onClick={() => onSort(k)}
    >
      {label} {active ? (sortDir === 'desc' ? '↓' : '↑') : ''}
    </th>
  );
}

function fmt(val: string | null, decimals = 2): string {
  if (val === null) return '—';
  const n = parseFloat(val);
  return isNaN(n) ? '—' : n.toFixed(decimals);
}

function TrendBadge({ badge }: { badge: string }) {
  if (badge === '▲') return <span className="text-green-500 font-bold">▲</span>;
  if (badge === '▼') return <span className="text-red-500 font-bold">▼</span>;
  return <span className="text-muted-foreground">—</span>;
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    validated: 'bg-green-100 text-green-800',
    rejected: 'bg-red-100 text-red-800',
    pending: 'bg-yellow-100 text-yellow-800',
    promoted: 'bg-blue-100 text-blue-800',
    paper_pending: 'bg-purple-100 text-purple-800',
    vetoed: 'bg-gray-100 text-gray-800',
  };
  const cls = colors[status] ?? 'bg-muted text-muted-foreground';
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${cls}`}>
      {status}
    </span>
  );
}

// Panel 1 — Cross-bot league table
function LeagueTablePanel() {
  const [sortKey, setSortKey] = React.useState<SortKey>('sharpe_30d');
  const [sortDir, setSortDir] = React.useState<SortDir>('desc');

  const { data, isLoading, error } = useQuery({
    queryKey: ['orchestrator', 'digest', 'latest'],
    queryFn: getDigestLatest,
    staleTime: 300_000,
  });

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  const sorted = React.useMemo(() => {
    if (!data) return [];
    return [...data].sort((a, b) => {
      const av = a[sortKey] === null ? null : parseFloat(a[sortKey] as string);
      const bv = b[sortKey] === null ? null : parseFloat(b[sortKey] as string);
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      return sortDir === 'desc' ? bv - av : av - bv;
    });
  }, [data, sortKey, sortDir]);

  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold">Bot League Table</h2>
      {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
      {error && <p className="text-sm text-red-500">Failed to load digest</p>}
      {data && data.length === 0 && (
        <p className="text-sm text-muted-foreground">No health data yet</p>
      )}
      {sorted.length > 0 && (
        <div className="overflow-x-auto rounded border border-border">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-muted/50">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase text-muted-foreground">
                  #
                </th>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase text-muted-foreground">
                  Bot
                </th>
                <SortHeader label="Sharpe 30d" k="sharpe_30d" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortHeader label="Drawdown" k="max_drawdown" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortHeader label="Win Rate" k="win_rate" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortHeader label="Adv. Acc." k="advisor_veto_accuracy_1h" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <th className="px-3 py-2 text-left text-xs font-medium uppercase text-muted-foreground">
                  Trend
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((bot, i) => (
                <tr
                  key={bot.bot_id}
                  className="border-b border-border last:border-0 hover:bg-muted/30"
                >
                  <td className="px-3 py-2 text-muted-foreground">{i + 1}</td>
                  <td className="px-3 py-2 font-medium">
                    <Link to="/bots/$botId" params={{ botId: bot.bot_id }} className="hover:underline">
                      {bot.bot_name}
                    </Link>
                  </td>
                  <td className="px-3 py-2 tabular-nums">{fmt(bot.sharpe_30d)}</td>
                  <td className="px-3 py-2 tabular-nums">{fmt(bot.max_drawdown)}</td>
                  <td className="px-3 py-2 tabular-nums">
                    {bot.win_rate !== null
                      ? `${(parseFloat(bot.win_rate) * 100).toFixed(1)}%`
                      : '—'}
                  </td>
                  <td className="px-3 py-2 tabular-nums">
                    {bot.advisor_veto_accuracy_1h !== null
                      ? `${(parseFloat(bot.advisor_veto_accuracy_1h) * 100).toFixed(1)}%`
                      : '—'}
                  </td>
                  <td className="px-3 py-2">
                    <TrendBadge badge={bot.trend_badge} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// Panel 2 — Portfolio exposure limits table
function ExposurePanel() {
  const { data, isLoading } = useQuery({
    queryKey: ['orchestrator', 'exposure-limits'],
    queryFn: getExposureLimits,
    staleTime: 60_000,
  });

  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold">Exposure Limits</h2>
      {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
      {data && data.length === 0 && (
        <p className="text-sm text-muted-foreground">No exposure limits configured</p>
      )}
      {data && data.length > 0 && (
        <div className="overflow-x-auto rounded border border-border">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-muted/50">
              <tr>
                {['Account', 'Type', 'Instrument', 'Sector', 'Max', 'Currency', 'Active'].map(
                  (h) => (
                    <th
                      key={h}
                      className="px-3 py-2 text-left text-xs font-medium uppercase text-muted-foreground"
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {data.map((lim) => (
                <tr
                  key={lim.id}
                  className="border-b border-border last:border-0 hover:bg-muted/30"
                >
                  <td className="px-3 py-2 font-mono text-xs">{lim.account_id.slice(0, 8)}…</td>
                  <td className="px-3 py-2">{lim.limit_type}</td>
                  <td className="px-3 py-2">{lim.instrument_id ?? '—'}</td>
                  <td className="px-3 py-2">{lim.sector ?? '—'}</td>
                  <td className="px-3 py-2 tabular-nums">{parseFloat(lim.max_notional).toLocaleString()}</td>
                  <td className="px-3 py-2">{lim.currency}</td>
                  <td className="px-3 py-2">
                    <span
                      className={`inline-block h-2 w-2 rounded-full ${lim.enabled ? 'bg-green-500' : 'bg-muted'}`}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// Panel 3 — Correlation matrix heatmap
function CorrelationPanel({ accountId }: { accountId: string | undefined }) {
  const { data, isLoading } = useQuery({
    queryKey: ['orchestrator', 'correlation', accountId],
    queryFn: () => getCorrelation(accountId as string),
    staleTime: 3_600_000,
    enabled: !!accountId,
  });

  if (!accountId) {
    return (
      <section>
        <h2 className="mb-2 text-sm font-semibold">Correlation Matrix</h2>
        <p className="text-sm text-muted-foreground">
          Pass <code className="font-mono">?account_id=…</code> to view correlation data.
        </p>
      </section>
    );
  }

  const keys = data ? Object.keys(data) : [];

  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold">Correlation Matrix</h2>
      {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
      {data && keys.length === 0 && (
        <p className="text-sm text-muted-foreground">No correlation data available</p>
      )}
      {data && keys.length > 0 && (
        <div className="overflow-x-auto">
          <table className="border-collapse text-xs">
            <thead>
              <tr>
                <th className="p-1" />
                {keys.map((k) => (
                  <th key={k} className="p-1 text-center font-mono text-muted-foreground">
                    {k}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {keys.map((row) => (
                <tr key={row}>
                  <td className="p-1 font-mono text-muted-foreground">{row}</td>
                  {keys.map((col) => {
                    const rho = data[row]?.[col] ?? 0;
                    const abs = Math.abs(rho);
                    const r = rho > 0 ? Math.round(rho * 200) : 0;
                    const b = rho < 0 ? Math.round(-rho * 200) : 0;
                    const border = abs > 0.7 ? '2px solid #f59e0b' : '1px solid transparent';
                    return (
                      <td
                        key={col}
                        className="p-1 text-center tabular-nums"
                        style={{
                          backgroundColor: `rgba(${r},0,${b},0.3)`,
                          border,
                          minWidth: '3rem',
                        }}
                        title={`ρ(${row},${col}) = ${rho.toFixed(3)}`}
                      >
                        {rho.toFixed(2)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// Panel 4 — Strategy generation feed
function StrategyGenPanel() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['strategy-gen', 'list'],
    queryFn: getGeneratedStrategies,
    staleTime: 60_000,
  });

  const approveMut = useMutation({
    mutationFn: approveStrategy,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['strategy-gen'] }),
  });
  const rejectMut = useMutation({
    mutationFn: rejectStrategy,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['strategy-gen'] }),
  });

  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold">Strategy Generation Feed</h2>
      {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
      {data && data.length === 0 && (
        <p className="text-sm text-muted-foreground">No generated strategies yet</p>
      )}
      {data && data.length > 0 && (
        <div className="space-y-2">
          {data.map((s) => (
            <div
              key={s.id}
              className="flex items-center gap-3 rounded border border-border px-3 py-2"
            >
              <StatusBadge status={s.sandbox_status} />
              <span className="flex-1 font-mono text-xs text-muted-foreground">
                {s.id.slice(0, 8)}…
              </span>
              {s.backtest_sharpe !== null && (
                <span className="text-xs tabular-nums">
                  Sharpe: <strong>{s.backtest_sharpe.toFixed(2)}</strong>
                </span>
              )}
              {s.sandbox_status === 'validated' && (
                <div className="flex gap-1">
                  <button
                    className="rounded bg-green-600 px-2 py-0.5 text-xs text-white hover:bg-green-700 disabled:opacity-50"
                    disabled={approveMut.isPending}
                    onClick={() => approveMut.mutate(s.id)}
                  >
                    Approve
                  </button>
                  <button
                    className="rounded bg-red-600 px-2 py-0.5 text-xs text-white hover:bg-red-700 disabled:opacity-50"
                    disabled={rejectMut.isPending}
                    onClick={() => rejectMut.mutate(s.id)}
                  >
                    Reject
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

interface OrchestrationSearch {
  account_id?: string;
}

export function OrchestrationPage(): React.JSX.Element {
  const { account_id } = routeApi.useSearch() as OrchestrationSearch;

  return (
    <main className="space-y-6 p-4">
      <h1 className="text-xl font-semibold">Orchestration</h1>
      <div className="grid gap-6 lg:grid-cols-2">
        <LeagueTablePanel />
        <ExposurePanel />
        <CorrelationPanel accountId={account_id} />
        <StrategyGenPanel />
      </div>
    </main>
  );
}
