import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { mintCsrfNonce } from '@/services/admin/api';
import {
  listParamSuggestions,
  rejectParamSuggestion,
  triggerParamSuggestion,
} from '@/services/param_tuner/api';
import type { ParamSuggestion, SuggestionStatus } from '@/services/param_tuner/types';

import { useParamTunerStream } from '../hooks/useParamTunerStream';
import { ParamCandidateCard } from './ParamCandidateCard';

interface Props {
  botId: string;
  isAdmin: boolean;
}

const ACTIVE_STATUSES: SuggestionStatus[] = ['pending', 'backtesting', 'ranked'];

function isActive(suggestion: ParamSuggestion): boolean {
  return ACTIVE_STATUSES.includes(suggestion.status);
}

export function ParamTunerSection({ botId, isAdmin }: Props) {
  useParamTunerStream(botId);
  const queryClient = useQueryClient();
  const queryKey = ['param-suggestions', botId] as const;

  const { data, isLoading, isError, error } = useQuery({
    queryKey,
    queryFn: () => listParamSuggestions(botId),
  });

  const triggerMut = useMutation({
    mutationFn: async () => {
      const nonce = await mintCsrfNonce();
      return triggerParamSuggestion(botId, nonce);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  const dismissMut = useMutation({
    mutationFn: async (suggestionId: string) => {
      const nonce = await mintCsrfNonce();
      await rejectParamSuggestion(botId, suggestionId, nonce);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  const suggestions = data?.items ?? [];
  const activeSuggestion = suggestions.find(isActive);
  const failedSuggestions = suggestions.filter((suggestion) => suggestion.status === 'failed');
  const showTrigger = isAdmin && activeSuggestion == null;

  return (
    <section className="space-y-4" aria-label="Parameter tuner">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">Parameter tuner</h3>
          <p className="text-xs text-muted-foreground">
            Generate, backtest, and approve candidate parameter sets.
          </p>
        </div>
        {showTrigger && (
          <button
            type="button"
            className="btn-primary text-xs"
            disabled={triggerMut.isPending}
            onClick={() => {
              triggerMut.mutate();
            }}
          >
            {triggerMut.isPending ? 'Triggering...' : 'Trigger'}
          </button>
        )}
      </div>

      {isLoading && <p className="text-sm text-muted-foreground">Loading suggestions...</p>}
      {isError && (
        <p role="alert" className="text-sm text-destructive">
          {error instanceof Error ? error.message : String(error)}
        </p>
      )}
      {triggerMut.isError && (
        <p role="alert" className="text-sm text-destructive">
          {triggerMut.error instanceof Error ? triggerMut.error.message : String(triggerMut.error)}
        </p>
      )}

      {activeSuggestion != null && (
        <div className="space-y-3 rounded border border-border p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">Suggestion {activeSuggestion.id}</span>
            <span className="rounded bg-muted px-2 py-1 text-xs">
              {activeSuggestion.status}
            </span>
            {activeSuggestion.status === 'backtesting' && (
              <span
                className="rounded bg-blue-100 px-2 py-1 text-xs text-blue-800"
                role="status"
              >
                Backtesting...
              </span>
            )}
          </div>
          {activeSuggestion.ai_reasoning != null && (
            <p className="text-sm text-muted-foreground">{activeSuggestion.ai_reasoning}</p>
          )}
          {activeSuggestion.status === 'ranked' && (
            <div className="space-y-3">
              {activeSuggestion.candidates.map((candidate, index) => (
                <ParamCandidateCard
                  key={`${activeSuggestion.id}-${index}`}
                  botId={botId}
                  suggestionId={activeSuggestion.id}
                  candidate={candidate}
                  index={index}
                  isAdmin={isAdmin}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {failedSuggestions.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-xs font-semibold uppercase text-muted-foreground">
            Failed suggestions
          </h4>
          {failedSuggestions.map((suggestion) => (
            <div
              key={suggestion.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded border border-border p-3"
            >
              <span className="text-sm">{suggestion.id}</span>
              {isAdmin && (
                <button
                  type="button"
                  className="btn-secondary text-xs"
                  disabled={dismissMut.isPending}
                  onClick={() => {
                    dismissMut.mutate(suggestion.id);
                  }}
                >
                  Dismiss
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
