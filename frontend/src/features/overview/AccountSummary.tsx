import * as React from 'react';

export function AccountSummary(): React.JSX.Element {
  return (
    <section className="flex h-full flex-col gap-2 p-4">
      <h2 className="text-sm font-semibold text-fg">Account Summary</h2>
      <p className="text-xs text-fg-muted">Loading account data…</p>
    </section>
  );
}
