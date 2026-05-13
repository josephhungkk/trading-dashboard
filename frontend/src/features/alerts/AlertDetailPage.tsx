import * as React from 'react';
import { useState } from 'react';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from '@tanstack/react-router';

import { DryRunPanel } from '@/features/alerts/DryRunPanel';
import { PredicateJsonEditor } from '@/features/alerts/PredicateJsonEditor';
import { PredicateVisualiser } from '@/features/alerts/PredicateVisualiser';
import {
  deleteAlert,
  getAlert,
  getAlertFires,
  putAlertStatus,
  putPredicate,
} from '@/services/alerts/api';
import type { AlertRule, RecentFire } from '@/services/alerts/types';
import { useDryRun } from '@/services/alerts/useDryRun';

interface AdminBodyShape {
  body?: { detail?: { schema_errors?: string[] } };
}

function extractSchemaErrors(err: unknown): string[] | null {
  const body = (err as AdminBodyShape).body;
  return body?.detail?.schema_errors ?? null;
}

export function AlertDetailPage(): React.JSX.Element {
  const { alertId } = useParams({ from: '/alerts/$alertId' });
  const id = Number(alertId);
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [schemaErrors, setSchemaErrors] = useState<string[]>([]);
  const [insufficientAck, setInsufficientAck] = useState(false);

  const query = useQuery({
    queryKey: ['alerts', 'detail', id],
    queryFn: (): Promise<AlertRule> => getAlert(id),
    enabled: Number.isFinite(id),
  });

  const fires = useQuery({
    queryKey: ['alerts', 'detail', id, 'fires'],
    queryFn: (): Promise<{ fires: RecentFire[] }> => getAlertFires(id, 50),
    enabled: Number.isFinite(id),
  });

  const dryRun = useDryRun();

  const savePredicate = useMutation({
    mutationFn: (predicate: Record<string, unknown>) => putPredicate(id, predicate),
    onSuccess: (updated) => {
      qc.setQueryData(['alerts', 'detail', id], updated);
      setEditing(false);
      setSchemaErrors([]);
    },
    onError: (err: unknown) => {
      const errs = extractSchemaErrors(err);
      if (errs) setSchemaErrors(errs);
    },
  });

  const toggleStatus = useMutation({
    mutationFn: (next: 'active' | 'disabled') => putAlertStatus(id, next),
    onSuccess: (updated) => {
      qc.setQueryData(['alerts', 'detail', id], updated);
    },
  });

  const remove = useMutation({
    mutationFn: () => deleteAlert(id),
    onSuccess: () => {
      window.location.assign('/alerts');
    },
  });

  if (!Number.isFinite(id)) {
    return (
      <div className="p-6 text-sm text-red-600" data-testid="alert-detail-error">
        invalid alert id
      </div>
    );
  }
  if (query.error instanceof Error) {
    return (
      <div className="p-6 text-sm text-red-600" data-testid="alert-detail-error">
        {query.error.message}
      </div>
    );
  }
  const rule = query.data;
  if (!rule) {
    return (
      <div className="p-6 text-sm text-muted-foreground" data-testid="alert-detail-loading">
        Loading…
      </div>
    );
  }

  const nextStatus: 'active' | 'disabled' =
    rule.status === 'active' ? 'disabled' : 'active';
  const canToggleStatus =
    rule.status === 'active'
    || rule.status === 'disabled'
    || rule.status === 'pending'
    || rule.status === 'dormant';

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="alert-detail-page">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{rule.user_label}</h1>
          <p className="text-xs text-muted-foreground">{rule.original_nl}</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-md bg-muted px-2 py-1 text-xs" data-testid="alert-detail-status">
            {rule.status}
          </span>
          {canToggleStatus && (
            <button
              type="button"
              onClick={() => toggleStatus.mutate(nextStatus)}
              disabled={toggleStatus.isPending}
              className="rounded-md border border-border px-3 py-1 text-sm hover:bg-muted disabled:opacity-50"
              data-testid="alert-detail-toggle-status"
            >
              {nextStatus === 'disabled' ? 'Disable' : 'Enable'}
            </button>
          )}
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            className="rounded-md border border-border px-3 py-1 text-sm hover:bg-muted"
            data-testid="alert-detail-edit-toggle"
          >
            {editing ? 'Cancel' : 'Edit predicate'}
          </button>
          <button
            type="button"
            onClick={() => remove.mutate()}
            className="rounded-md border border-border px-3 py-1 text-sm text-red-600 hover:bg-red-50"
            data-testid="alert-detail-delete"
          >
            Delete
          </button>
        </div>
      </header>

      {editing ? (
        <PredicateJsonEditor
          initial={rule.predicate_json}
          onSave={(p) => savePredicate.mutate(p)}
          saving={savePredicate.isPending}
          schemaErrors={schemaErrors}
        />
      ) : (
        <PredicateVisualiser predicate={rule.predicate_json} />
      )}

      <DryRunPanel
        result={dryRun.data ?? null}
        isPending={dryRun.isPending}
        insufficientAcknowledged={insufficientAck}
        onAcknowledge={setInsufficientAck}
        onReRun={() => dryRun.mutate(rule.predicate_json)}
      />

      <section
        className="rounded-md border border-border bg-panel p-4"
        data-testid="alert-detail-fires"
      >
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Recent fires
        </h2>
        {fires.isLoading ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : (fires.data?.fires.length ?? 0) === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="alert-detail-fires-empty">
            No fires yet.
          </p>
        ) : (
          <ul className="space-y-1">
            {(fires.data?.fires ?? []).map((fire) => (
              <li
                key={fire.id}
                className="flex justify-between rounded-md bg-muted/50 px-2 py-1 text-xs tabular-nums"
                data-testid={`alert-detail-fire-${fire.id}`}
              >
                <span className="font-mono">{fire.fired_at}</span>
                <span>{fire.verdict}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
