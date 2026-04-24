import * as React from 'react';
import { Activity } from 'lucide-react';
import { EmptyState } from '@/components/patterns/EmptyState';

export function TradeStubPage(): React.JSX.Element {
  return (
    <section className="flex h-full min-h-0 flex-col p-4" aria-label="Trade">
      <EmptyState
        icon={Activity}
        title="Order ticket lands in Phase 5"
        description="For now, use your broker's native UI."
        className="flex-1"
      />
    </section>
  );
}
