import * as React from 'react';
import { useState } from 'react';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { CreateAlertModal } from '@/features/alerts/CreateAlertModal';
import { deleteAlert, listAlerts } from '@/services/alerts/api';
import type { AlertRule } from '@/services/alerts/types';

type Tab = 'active' | 'dormant' | 'disabled';

const TABS: { key: Tab; label: string }[] = [
  { key: 'active', label: 'Active' },
  { key: 'dormant', label: 'Dormant' },
  { key: 'disabled', label: 'Disabled' },
];

const ALERTS_QUERY_KEY = ['alerts', 'list'] as const;

function statusToTab(status: string): Tab {
  if (status === 'active' || status === 'pending') return 'active';
  if (status === 'dormant') return 'dormant';
  return 'disabled';
}

export function AlertsPage(): React.JSX.Element {
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>('active');
  const [modalOpen, setModalOpen] = useState(false);

  const query = useQuery({
    queryKey: ALERTS_QUERY_KEY,
    queryFn: async (): Promise<AlertRule[]> => {
      const resp = await listAlerts();
      return resp.alerts;
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => deleteAlert(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ALERTS_QUERY_KEY });
    },
  });

  const filtered = (query.data ?? []).filter((r) => statusToTab(r.status) === tab);
  const errorMessage =
    query.error instanceof Error ? query.error.message : null;

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="alerts-page">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Alerts</h1>
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground"
          data-testid="alerts-new"
        >
          New Alert
        </button>
      </header>

      <div
        role="tablist"
        aria-label="Alert status filter"
        className="flex gap-1 border-b border-border"
      >
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`px-3 py-2 text-sm ${
              tab === t.key
                ? 'border-b-2 border-primary font-medium'
                : 'text-muted-foreground'
            }`}
            data-testid={`alerts-tab-${t.key}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {errorMessage && (
        <p
          role="alert"
          className="rounded-md border border-red-300 bg-red-50 p-2 text-sm text-red-900"
        >
          {errorMessage}
        </p>
      )}

      {query.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : filtered.length === 0 ? (
        <p
          className="text-sm text-muted-foreground"
          data-testid={`alerts-empty-${tab}`}
        >
          No {tab} alerts.
        </p>
      ) : (
        <ul className="space-y-2" data-testid="alerts-list">
          {filtered.map((rule) => (
            <li
              key={rule.id}
              className="flex items-center justify-between rounded-md border border-border bg-panel p-3"
              data-testid={`alerts-row-${rule.id}`}
            >
              <div className="flex flex-col">
                <span className="font-medium">{rule.user_label}</span>
                <span className="text-xs text-muted-foreground">
                  {rule.original_nl}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="rounded-md bg-muted px-2 py-0.5 text-xs">
                  {rule.status}
                </span>
                <a
                  href={`/alerts/${rule.id}`}
                  className="rounded-md border border-border px-2 py-1 text-xs hover:bg-muted"
                  data-testid={`alerts-open-${rule.id}`}
                >
                  Open
                </a>
                <button
                  type="button"
                  onClick={() => remove.mutate(rule.id)}
                  className="rounded-md border border-border px-2 py-1 text-xs text-red-600 hover:bg-red-50"
                  data-testid={`alerts-delete-${rule.id}`}
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <CreateAlertModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={() => {
          void qc.invalidateQueries({ queryKey: ALERTS_QUERY_KEY });
        }}
      />
    </div>
  );
}
