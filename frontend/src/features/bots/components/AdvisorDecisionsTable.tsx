import * as React from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { getAdvisorDecisions } from '../../../services/advisor/api';
import type { AdvisorDecision, AdvisorVerdict } from '../../../services/advisor/types';
import { AdvisorDecisionDrawer } from './AdvisorDecisionDrawer';

interface Props {
  botId: string;
}

const VERDICT_CLASS: Record<AdvisorVerdict, string> = {
  approve: 'bg-green-100 text-green-800',
  veto: 'bg-red-100 text-red-800',
  fail_open: 'bg-yellow-100 text-yellow-800',
};

function formatConfidence(value: number | null): string {
  return value == null ? 'N/A' : `${Math.round(value * 100)}%`;
}

export function AdvisorDecisionsTable({ botId }: Props): React.JSX.Element {
  const [selected, setSelected] = React.useState<AdvisorDecision | null>(null);
  const query = useInfiniteQuery({
    queryKey: ['bot', botId, 'advisor-decisions'],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      getAdvisorDecisions(botId, pageParam === undefined ? undefined : { before: pageParam }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const decisions = query.data?.pages.flatMap((page) => page.items) ?? [];

  if (query.isError && decisions.length === 0) {
    return (
      <section className="mt-6 space-y-3">
        <h2 className="text-sm font-semibold">Advisor decisions</h2>
        <p role="alert" className="text-xs text-destructive">Failed to load advisor decisions.</p>
      </section>
    );
  }

  return (
    <section className="mt-6 space-y-3">
      <h2 className="text-sm font-semibold">Advisor decisions</h2>
      {query.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : decisions.length === 0 ? (
        <p className="rounded border border-border p-3 text-sm text-muted-foreground">
          No advisor decisions yet.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th scope="col" className="py-2 pr-3 font-medium">Created</th>
                <th scope="col" className="py-2 pr-3 font-medium">Verdict</th>
                <th scope="col" className="py-2 pr-3 font-medium">Canonical ID</th>
                <th scope="col" className="py-2 pr-3 font-medium">Side</th>
                <th scope="col" className="py-2 pr-3 font-medium">Qty</th>
                <th scope="col" className="py-2 pr-3 font-medium">Confidence</th>
                <th scope="col" className="py-2 pr-3 font-medium">Latency</th>
                <th scope="col" className="py-2 pr-3 font-medium">Mode</th>
              </tr>
            </thead>
            <tbody>
              {decisions.map((decision) => (
                <tr
                  key={decision.id}
                  tabIndex={0}
                  onClick={() => setSelected(decision)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') setSelected(decision);
                  }}
                  className="cursor-pointer border-b hover:bg-muted/50"
                >
                  <td className="py-2 pr-3">{new Date(decision.created_at).toLocaleString()}</td>
                  <td className="py-2 pr-3">
                    <span className={`rounded px-2 py-1 text-xs ${VERDICT_CLASS[decision.verdict]}`}>
                      {decision.verdict}
                    </span>
                  </td>
                  <td className="py-2 pr-3">{decision.canonical_id}</td>
                  <td className="py-2 pr-3">
                    {(decision.intent as Record<string, unknown>)?.side as string ?? '—'}
                  </td>
                  <td className="py-2 pr-3">
                    {(decision.intent as Record<string, unknown>)?.qty as string ?? '—'}
                  </td>
                  <td className="py-2 pr-3">{formatConfidence(decision.confidence)}</td>
                  <td className="py-2 pr-3">{decision.latency_ms} ms</td>
                  <td className="py-2 pr-3">{decision.effective_mode}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {query.hasNextPage && (
        <button
          type="button"
          onClick={() => void query.fetchNextPage()}
          disabled={query.isFetchingNextPage}
          className="btn-secondary text-xs"
        >
          {query.isFetchingNextPage ? 'Loading…' : 'Load more'}
        </button>
      )}

      {query.isError && decisions.length > 0 && (
        <p role="alert" className="text-xs text-destructive">Failed to load advisor decisions.</p>
      )}

      <AdvisorDecisionDrawer decision={selected} onClose={() => setSelected(null)} />
    </section>
  );
}
