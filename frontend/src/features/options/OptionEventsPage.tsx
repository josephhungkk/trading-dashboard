import * as React from 'react';

import { useExerciseElections } from '@/features/options/hooks/useExerciseElections';
import type { ExerciseElection } from '@/features/options/types';

function formatDate(isoString: string): string {
  const d = new Date(isoString);
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

function ElectionRow({ row }: { readonly row: ExerciseElection }): React.JSX.Element {
  return (
    <tr className="border-b border-border last:border-0">
      <td className="py-3 pr-4 text-sm">{formatDate(row.expiry_date)}</td>
      <td className="py-3 pr-4 text-sm capitalize">{row.action}</td>
      <td className="py-3 pr-4 text-sm">
        <span
          className={
            row.status === 'confirmed'
              ? 'text-green-600'
              : row.status === 'pending'
                ? 'text-amber-600'
                : 'text-muted-foreground'
          }
        >
          {row.status}
        </span>
      </td>
      <td className="py-3 text-sm font-mono text-muted-foreground">
        {row.broker_ref ?? '—'}
      </td>
    </tr>
  );
}

export function OptionEventsPage(): React.JSX.Element {
  const elections = useExerciseElections();

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="option-events-page">
      <h1 className="text-2xl font-semibold">Exercise Elections</h1>

      {elections.isLoading && (
        <p className="text-sm text-muted-foreground" data-testid="elections-loading">
          Loading exercise elections…
        </p>
      )}

      {elections.isError && (
        <p className="text-sm text-red-600" data-testid="elections-error">
          Failed to load elections:{' '}
          {elections.error instanceof Error ? elections.error.message : 'unknown error'}
        </p>
      )}

      {elections.data && elections.data.length === 0 && (
        <p className="text-sm text-muted-foreground" data-testid="elections-empty">
          No pending exercise elections.
        </p>
      )}

      {elections.data && elections.data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left" data-testid="elections-table">
            <thead>
              <tr className="border-b border-border text-xs font-medium uppercase text-muted-foreground">
                <th className="pb-2 pr-4">Date</th>
                <th className="pb-2 pr-4">Action</th>
                <th className="pb-2 pr-4">Status</th>
                <th className="pb-2">Broker Ref</th>
              </tr>
            </thead>
            <tbody>
              {elections.data.map((row) => (
                <ElectionRow key={row.id} row={row} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
