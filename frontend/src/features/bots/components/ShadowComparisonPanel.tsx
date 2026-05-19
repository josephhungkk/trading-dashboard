import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { mintCsrfNonce } from '@/services/admin/api';
import {
  createShadow,
  getShadowComparison,
  promoteShadow,
} from '@/services/shadow_promoter/api';
import type { ShadowComparisonReport } from '@/services/shadow_promoter/types';

import { useShadowStream } from '../hooks/useShadowStream';
import { ShadowMetricsTable } from './ShadowMetricsTable';

interface Props {
  botId: string;
  isAdmin: boolean;
}

function parseOverrideParams(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('Override params must be a JSON object');
  }
  return parsed as Record<string, unknown>;
}

export function ShadowComparisonPanel({ botId, isAdmin }: Props) {
  useShadowStream(botId);
  const [overrideParams, setOverrideParams] = useState('{}');
  const [comparisonWindowDays, setComparisonWindowDays] = useState('14');
  const [formError, setFormError] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const queryKey = ['shadow-comparison', botId] as const;

  const { data, isLoading, isError, error } = useQuery<ShadowComparisonReport>({
    queryKey,
    queryFn: () => getShadowComparison(botId),
  });

  const createMut = useMutation({
    mutationFn: async () => {
      const days = Number(comparisonWindowDays);
      if (!Number.isFinite(days) || days < 1 || days > 90) {
        throw new Error('Comparison window must be between 1 and 90 days');
      }
      const nonce = await mintCsrfNonce();
      return createShadow(botId, parseOverrideParams(overrideParams), days, nonce);
    },
    onSuccess: () => {
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey });
    },
    onError: (err) => {
      setFormError((err as Error).message);
    },
  });

  const promoteMut = useMutation({
    mutationFn: async (shadowId: string) => {
      const nonce = await mintCsrfNonce();
      await promoteShadow(botId, shadowId, nonce);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  return (
    <section className="space-y-4" aria-label="Shadow comparison">
      <div>
        <h3 className="text-sm font-semibold">Shadows</h3>
        <p className="text-xs text-muted-foreground">
          Compare paper shadow bots against live performance before promotion.
        </p>
      </div>

      {isAdmin && (
        <form
          className="space-y-3 rounded border border-border p-3"
          onSubmit={(e) => {
            e.preventDefault();
            setFormError(null);
            createMut.mutate();
          }}
        >
          <label className="block text-sm font-medium" htmlFor="shadow_override_params">
            Override params
          </label>
          <textarea
            id="shadow_override_params"
            className="min-h-24 w-full rounded border border-border bg-background p-2 font-mono text-sm"
            value={overrideParams}
            onChange={(e) => setOverrideParams(e.target.value)}
          />

          <label className="block text-sm font-medium" htmlFor="comparison_window_days">
            Comparison window days
          </label>
          <input
            id="comparison_window_days"
            type="number"
            min="1"
            max="90"
            value={comparisonWindowDays}
            onChange={(e) => setComparisonWindowDays(e.target.value)}
          />

          <button type="submit" className="btn-primary text-xs" disabled={createMut.isPending}>
            {createMut.isPending ? 'Creating...' : 'Create shadow'}
          </button>
          {formError != null && (
            <p role="alert" className="text-sm text-destructive">
              {formError}
            </p>
          )}
        </form>
      )}

      {isLoading && <p className="text-sm text-muted-foreground">Loading shadows...</p>}
      {isError && (
        <p role="alert" className="text-sm text-destructive">
          {(error as Error).message}
        </p>
      )}

      <div className="space-y-4">
        {(data?.shadows ?? []).map((shadow) => (
          <article key={shadow.shadow_bot_id} className="space-y-3 rounded border border-border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="space-y-1">
                <h4 className="text-sm font-semibold">Shadow {shadow.shadow_bot_id}</h4>
                <p className="text-xs text-muted-foreground">
                  Window {shadow.shadow_metrics.window_days} days
                </p>
              </div>
              <div className="flex items-center gap-2">
                {!shadow.comparison_ready && (
                  <span className="rounded bg-amber-100 px-2 py-1 text-xs text-amber-800">
                    Not yet ready
                  </span>
                )}
                {isAdmin && (
                  <button
                    type="button"
                    className="btn-primary text-xs"
                    disabled={!shadow.comparison_ready || promoteMut.isPending}
                    onClick={() => {
                      if (window.confirm('Promote this shadow to live?')) {
                        promoteMut.mutate(shadow.shadow_bot_id);
                      }
                    }}
                  >
                    {promoteMut.isPending ? 'Promoting...' : 'Promote'}
                  </button>
                )}
              </div>
            </div>
            <ShadowMetricsTable shadow={shadow} />
          </article>
        ))}
      </div>

      {promoteMut.isError && (
        <p role="alert" className="text-sm text-destructive">
          {(promoteMut.error as Error).message}
        </p>
      )}
    </section>
  );
}
