import * as React from 'react';
import { CapabilityMapEditor } from '@/features/admin/ai/CapabilityMapEditor';
import { CostLedgerView } from '@/features/admin/ai/CostLedgerView';
import { HeavyBoxStateBadge } from '@/features/admin/ai/HeavyBoxStateBadge';
import { ProviderKeyCrud } from '@/features/admin/ai/ProviderKeyCrud';

export function AdminAiPage(): React.JSX.Element {
  return (
    <section className="flex min-h-0 flex-1 flex-col gap-4 p-4">
      <header>
        <h1 className="text-xl font-semibold text-fg">AI router admin</h1>
      </header>

      <AiSection title="Capability map editor">
        <CapabilityMapEditor />
      </AiSection>
      <AiSection title="Provider key CRUD">
        <ProviderKeyCrud />
      </AiSection>
      <AiSection title="Cost ledger">
        <CostLedgerView />
      </AiSection>
      <AiSection title="Heavy-box state">
        <HeavyBoxStateBadge />
      </AiSection>
    </section>
  );
}

function AiSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <details open className="rounded-md border border-border bg-bg">
      <summary
        className="cursor-pointer px-3 py-2 text-base font-medium text-fg"
        aria-label={`Section: ${title}`}
      >
        {title}
      </summary>
      <div className="border-t border-border p-3">
        {children}
      </div>
    </details>
  );
}
