import * as React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  fetchRollRules,
  fetchSettlements,
  fetchRollPreview,
  deleteRollRule,
} from '@/services/futures/api';
import type {
  FutureSettlementEvent,
  FutureRollRule,
  RollPreviewResponse,
} from '@/services/futures/types';
import { RollConfirmDialog } from './RollConfirmDialog';

type Tab = 'positions' | 'settlements';

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function SettlementRow({ event }: { readonly event: FutureSettlementEvent }): React.JSX.Element {
  return (
    <tr className="border-b border-border last:border-0">
      <td className="py-3 pr-4 text-sm">{event.instrument_id}</td>
      <td className="py-3 pr-4 text-sm font-mono">{event.settlement_price}</td>
      <td className="py-3 pr-4 text-sm font-mono">
        {event.cash_delta != null ? event.cash_delta : '—'}
      </td>
      <td className="py-3 pr-4 text-sm">
        <span
          className={
            event.settlement_type === 'PHYSICAL' ? 'text-amber-600' : 'text-muted-foreground'
          }
        >
          {event.settlement_type}
        </span>
      </td>
      <td className="py-3 text-sm text-muted-foreground">{formatDate(event.settled_at)}</td>
    </tr>
  );
}

function RollRuleRow({
  rule,
  onDelete,
  onRoll,
}: {
  readonly rule: FutureRollRule;
  readonly onDelete: (id: string) => void;
  readonly onRoll: (instrumentId: number) => void;
}): React.JSX.Element {
  return (
    <tr className="border-b border-border last:border-0">
      <td className="py-3 pr-4 text-sm">{rule.instrument_id}</td>
      <td className="py-3 pr-4 text-sm">{rule.days_before} days</td>
      <td className="py-3 pr-4 text-sm">
        <span className={rule.enabled ? 'text-green-600' : 'text-muted-foreground'}>
          {rule.enabled ? 'Enabled' : 'Disabled'}
        </span>
      </td>
      <td className="py-3 text-sm space-x-3">
        <button
          type="button"
          onClick={() => onRoll(rule.instrument_id)}
          className="text-primary hover:underline text-xs"
        >
          Roll
        </button>
        <button
          type="button"
          onClick={() => onDelete(rule.id)}
          className="text-red-600 hover:underline text-xs"
        >
          Delete
        </button>
      </td>
    </tr>
  );
}

export function FuturesPage(): React.JSX.Element {
  const [activeTab, setActiveTab] = React.useState<Tab>('positions');
  const [rollPreview, setRollPreview] = React.useState<RollPreviewResponse | null>(null);
  const queryClient = useQueryClient();

  const rulesQuery = useQuery({
    queryKey: ['futures', 'roll-rules'],
    queryFn: fetchRollRules,
    staleTime: 30_000,
  });

  const settlementsQuery = useQuery({
    queryKey: ['futures', 'settlements'],
    queryFn: () => fetchSettlements(),
    staleTime: 60_000,
    enabled: activeTab === 'settlements',
  });

  const deleteMutation = useMutation({
    mutationFn: deleteRollRule,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['futures', 'roll-rules'] });
    },
  });

  const handleRoll = async (instrumentId: number): Promise<void> => {
    const preview = await fetchRollPreview(instrumentId, '');
    setRollPreview(preview);
  };

  return (
    <main className="p-4 md:p-6 max-w-5xl mx-auto space-y-6">
      <h1 className="text-xl font-semibold">Futures</h1>

      {/* Tab bar */}
      <div className="flex gap-2 border-b border-border">
        {(['positions', 'settlements'] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            onClick={() => setActiveTab(tab)}
            className={
              `px-4 py-2 text-sm capitalize -mb-px border-b-2 transition-colors ` +
              (activeTab === tab
                ? 'border-primary text-foreground font-medium'
                : 'border-transparent text-muted-foreground hover:text-foreground')
            }
          >
            {tab}
          </button>
        ))}
      </div>

      {activeTab === 'positions' && (
        <section className="space-y-4">
          <h2 className="text-base font-medium">Roll Rules</h2>
          {rulesQuery.isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
          {rulesQuery.isError && (
            <p className="text-sm text-red-600">Failed to load roll rules.</p>
          )}
          {rulesQuery.data != null && rulesQuery.data.length === 0 && (
            <p className="text-sm text-muted-foreground">No roll rules configured.</p>
          )}
          {rulesQuery.data != null && rulesQuery.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-border">
                    <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Instrument</th>
                    <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Roll before</th>
                    <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Status</th>
                    <th className="pb-2 text-xs font-medium text-muted-foreground">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rulesQuery.data.map((rule) => (
                    <RollRuleRow
                      key={rule.id}
                      rule={rule}
                      onDelete={(id) => deleteMutation.mutate(id)}
                      onRoll={(id) => void handleRoll(id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {activeTab === 'settlements' && (
        <section className="space-y-4">
          <h2 className="text-base font-medium">Settlement Events</h2>
          {settlementsQuery.isLoading && (
            <p className="text-sm text-muted-foreground">Loading…</p>
          )}
          {settlementsQuery.isError && (
            <p className="text-sm text-red-600">Failed to load settlements.</p>
          )}
          {settlementsQuery.data?.items != null &&
            settlementsQuery.data.items.length === 0 && (
              <p className="text-sm text-muted-foreground">No settlement events.</p>
            )}
          {settlementsQuery.data?.items != null &&
            settlementsQuery.data.items.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Instrument</th>
                      <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Price</th>
                      <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">P&L</th>
                      <th className="pb-2 pr-4 text-xs font-medium text-muted-foreground">Type</th>
                      <th className="pb-2 text-xs font-medium text-muted-foreground">Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {settlementsQuery.data.items.map((e) => (
                      <SettlementRow key={e.id} event={e} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </section>
      )}

      {rollPreview != null && (
        <RollConfirmDialog
          preview={rollPreview}
          onClose={() => setRollPreview(null)}
          onConfirmed={() => {
            setRollPreview(null);
            void queryClient.invalidateQueries({ queryKey: ['futures'] });
          }}
        />
      )}
    </main>
  );
}
