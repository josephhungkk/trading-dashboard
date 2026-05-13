import * as React from 'react';

export function CostLedgerView(): React.JSX.Element {
  return (
    <div className="rounded-md border border-border bg-panel p-3">
      <h2 className="text-base font-semibold text-fg">Cost ledger</h2>
      <p className="mt-2 text-sm text-fg-muted">
        Coming in phase 11b — needs `GET /api/ai/cost-ledger` endpoint.
      </p>
    </div>
  );
}
