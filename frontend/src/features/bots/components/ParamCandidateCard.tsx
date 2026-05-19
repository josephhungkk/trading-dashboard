import { useMutation, useQueryClient } from '@tanstack/react-query';

import { mintCsrfNonce } from '@/services/admin/api';
import { approveParamSuggestion, rejectParamSuggestion } from '@/services/param_tuner/api';
import type { ParamCandidate } from '@/services/param_tuner/types';

interface Props {
  botId: string;
  suggestionId: string;
  candidate: ParamCandidate;
  index: number;
  isAdmin: boolean;
}

function metric(value: number | null | undefined, digits = 2): string {
  return value == null ? '--' : value.toFixed(digits);
}

function deltaClass(value: string): string {
  if (value.startsWith('+')) return 'bg-green-100 text-green-800';
  if (value.startsWith('-')) return 'bg-red-100 text-red-800';
  return 'bg-muted text-muted-foreground';
}

export function ParamCandidateCard({
  botId,
  suggestionId,
  candidate,
  index,
  isAdmin,
}: Props) {
  const queryClient = useQueryClient();
  const queryKey = ['param-suggestions', botId] as const;

  const approveMut = useMutation({
    mutationFn: async () => {
      const nonce = await mintCsrfNonce();
      await approveParamSuggestion(botId, suggestionId, index, nonce);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  const rejectMut = useMutation({
    mutationFn: async () => {
      const nonce = await mintCsrfNonce();
      await rejectParamSuggestion(botId, suggestionId, nonce);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  const result = candidate.backtest_result;
  const mutationError = approveMut.error ?? rejectMut.error;

  return (
    <article
      className="space-y-3 rounded border border-border p-3"
      data-testid="param-candidate-card"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="rounded bg-primary px-2 py-1 text-xs font-semibold text-primary-foreground">
            Rank {candidate.rank ?? index + 1}
          </span>
          {candidate.backtest_job_id != null && (
            <span className="text-xs text-muted-foreground">
              Backtest {candidate.backtest_job_id}
            </span>
          )}
        </div>
        {isAdmin && (
          <div className="flex gap-2">
            <button
              type="button"
              className="btn-primary text-xs"
              disabled={approveMut.isPending || rejectMut.isPending}
              onClick={() => {
                approveMut.mutate();
              }}
            >
              {approveMut.isPending ? 'Approving...' : 'Approve this'}
            </button>
            <button
              type="button"
              className="btn-secondary text-xs"
              disabled={approveMut.isPending || rejectMut.isPending}
              onClick={() => {
                rejectMut.mutate();
              }}
            >
              {rejectMut.isPending ? 'Rejecting...' : 'Reject all'}
            </button>
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-2 text-xs">
        <span className="rounded bg-muted px-2 py-1">Sharpe {metric(result?.sharpe)}</span>
        <span className="rounded bg-muted px-2 py-1">MAR {metric(result?.mar)}</span>
        <span className="rounded bg-muted px-2 py-1">Max DD {metric(result?.max_dd)}%</span>
        <span className="rounded bg-muted px-2 py-1">
          Win {result?.win_rate == null ? '--' : `${(result.win_rate * 100).toFixed(1)}%`}
        </span>
        <span className="rounded bg-muted px-2 py-1">
          Trades {result?.total_trades ?? '--'}
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {Object.entries(candidate.delta_vs_current).map(([key, value]) => (
          <span key={key} className={`rounded px-2 py-1 text-xs ${deltaClass(value)}`}>
            {key}: {value}
          </span>
        ))}
      </div>

      <pre className="overflow-x-auto rounded bg-muted px-3 py-2 text-xs">
        {JSON.stringify(candidate.params, null, 2)}
      </pre>

      {(approveMut.isError || rejectMut.isError) && (
        <p role="alert" className="text-xs text-destructive">
          {mutationError instanceof Error ? mutationError.message : String(mutationError)}
        </p>
      )}
    </article>
  );
}
