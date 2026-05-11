/**
 * Phase 10a E4 — read-only audit feed of risk_decisions rows.
 *
 * Filterable by verdict (all/allow/warn/block) and free-text account_id.
 * Backed by /api/risk/decisions with the same query the BE uses.
 */

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Input } from '@/components/primitives/Input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/primitives/Select';
import { listRiskDecisions } from '@/services/risk/api';
import type {
  RiskDecisionOut,
  RiskDecisionsFilter,
  RiskVerdict,
} from '@/services/risk/types';

type VerdictFilter = RiskVerdict | 'all';

const VERDICT_FILTERS: readonly { value: VerdictFilter; label: string }[] = [
  { value: 'all', label: 'All verdicts' },
  { value: 'allow', label: 'Allow' },
  { value: 'warn', label: 'Warn' },
  { value: 'block', label: 'Block' },
];

const VERDICT_CLASS: Record<RiskVerdict, string> = {
  allow: 'border-success/60 bg-success/10 text-success',
  warn: 'border-warning/60 bg-warning/10 text-warning',
  block: 'border-destructive/60 bg-destructive/10 text-destructive',
};

function formatEvaluatedAt(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function RiskDecisionsPage(): React.JSX.Element {
  const [verdict, setVerdict] = React.useState<VerdictFilter>('all');
  const [accountId, setAccountId] = React.useState('');
  const trimmedAccountId = accountId.trim();

  const query = useQuery<RiskDecisionOut[], Error>({
    queryKey: ['risk-decisions', verdict, trimmedAccountId],
    queryFn: () => {
      const filter: RiskDecisionsFilter = { limit: 50 };
      if (verdict !== 'all') filter.verdict = verdict;
      if (trimmedAccountId !== '') filter.account_id = trimmedAccountId;
      return listRiskDecisions(filter);
    },
    staleTime: 5_000,
  });

  return (
    <section className="flex flex-col gap-4 p-4">
      <header className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold">Risk decisions</h1>
        <p className="text-sm text-fg-muted">
          Most recent 50 risk-gate verdicts. Filter by account or verdict;
          updates every refresh.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3">
        <label htmlFor="risk-decisions-verdict" className="flex flex-col gap-1 text-sm">
          <span className="font-medium">Verdict</span>
          <Select
            value={verdict}
            onValueChange={(value) => setVerdict(value as VerdictFilter)}
          >
            <SelectTrigger id="risk-decisions-verdict">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {VERDICT_FILTERS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>

        <label htmlFor="risk-decisions-account" className="flex flex-col gap-1 text-sm">
          <span className="font-medium">Account ID</span>
          <Input
            id="risk-decisions-account"
            value={accountId}
            onChange={(event) => setAccountId(event.currentTarget.value)}
            placeholder="UUID (leave blank for all)"
          />
        </label>
      </div>

      {query.isLoading ? (
        <p className="text-sm text-fg-muted">Loading decisions…</p>
      ) : query.error ? (
        <p role="alert" className="text-sm text-destructive">{query.error.message}</p>
      ) : query.data && query.data.length === 0 ? (
        <p className="text-sm text-fg-muted">No decisions match the filters.</p>
      ) : (
        <div className="overflow-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-panel-muted text-left text-fg-muted">
              <tr>
                <th className="p-2">Evaluated at</th>
                <th className="p-2">Account</th>
                <th className="p-2">Side</th>
                <th className="p-2">Qty</th>
                <th className="p-2">Price</th>
                <th className="p-2">Verdict</th>
                <th className="p-2">Blockers</th>
                <th className="p-2">Warnings</th>
                <th className="p-2">Attempt</th>
                <th className="p-2">Latency (ms)</th>
              </tr>
            </thead>
            <tbody>
              {query.data?.map((row) => (
                <tr key={row.id} className="border-t border-border">
                  <td className="p-2 font-mono">{formatEvaluatedAt(row.evaluated_at)}</td>
                  <td className="p-2 font-mono">{row.account_id.slice(0, 8)}</td>
                  <td className="p-2">{row.side}</td>
                  <td className="p-2 font-mono">{row.qty}</td>
                  <td className="p-2 font-mono">{row.price ?? '—'}</td>
                  <td className="p-2">
                    <span
                      className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${VERDICT_CLASS[row.verdict as RiskVerdict]}`}
                    >
                      {row.verdict}
                    </span>
                  </td>
                  <td className="p-2 text-center">{row.blockers.length}</td>
                  <td className="p-2 text-center">{row.warnings.length}</td>
                  <td className="p-2">{row.attempt_kind}</td>
                  <td className="p-2 font-mono">{row.latency_ms}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
