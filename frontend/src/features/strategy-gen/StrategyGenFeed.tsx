import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { listStrategies } from '../../services/strategy-gen/api';
import type { GeneratedStrategy, SandboxStatus } from '../../services/strategy-gen/types';

const STATUS_BADGE: Record<SandboxStatus, string> = {
  pending: 'bg-yellow-100 text-yellow-800',
  validated: 'bg-blue-100 text-blue-800',
  rejected: 'bg-red-100 text-red-800',
  promoted: 'bg-green-100 text-green-800',
};

export function StrategyGenFeed(): React.JSX.Element {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['strategy-gen'],
    queryFn: listStrategies,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  if (isLoading) {
    return <div className="p-4 text-sm text-muted-foreground">Loading strategies...</div>;
  }

  if (isError) {
    return <div className="p-4 text-sm text-destructive">Failed to load strategies.</div>;
  }

  const strategies = data ?? [];

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h2 className="mb-3 text-base font-semibold">Strategy Generation Feed</h2>
      {strategies.length === 0 ? (
        <p className="text-sm text-muted-foreground">No strategies generated yet.</p>
      ) : (
        <ul className="divide-y divide-border">
          {strategies.map((s) => (
            <StrategyRow key={s.id} strategy={s} />
          ))}
        </ul>
      )}
    </div>
  );
}

function StrategyRow({ strategy: s }: { strategy: GeneratedStrategy }): React.JSX.Element {
  return (
    <li className="flex items-center justify-between gap-4 py-2">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{s.name}</p>
        <p className="text-xs text-muted-foreground">{s.llm_model}</p>
      </div>
      <span
        className={`rounded-full px-2 py-1 text-xs font-medium ${STATUS_BADGE[s.sandbox_status]}`}
      >
        {s.sandbox_status}
      </span>
      {s.sandbox_error && (
        <span className="max-w-xs truncate text-xs text-destructive" title={s.sandbox_error}>
          {s.sandbox_error}
        </span>
      )}
    </li>
  );
}
