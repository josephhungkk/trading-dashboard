import * as React from 'react';
import { Bell } from 'lucide-react';
import { EmptyState } from '@/components/patterns/EmptyState';

export function AlertsStubPage(): React.JSX.Element {
  return (
    <section className="flex h-full min-h-0 flex-col p-4" aria-label="Alerts">
      <EmptyState
        icon={Bell}
        title="Alerts land in Phase 7"
        description="Telegram + email alerts for price/order events."
        className="flex-1"
      />
    </section>
  );
}
