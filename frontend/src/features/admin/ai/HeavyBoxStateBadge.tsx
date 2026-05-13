import * as React from 'react';
import { Badge } from '@/components/primitives/Badge';

export function HeavyBoxStateBadge(): React.JSX.Element {
  return (
    <div className="rounded-md border border-border bg-panel p-3">
      <h2 className="text-base font-semibold text-fg">Heavy-box state</h2>
      <Badge className="mt-2">Pending phase-11b BE endpoint</Badge>
    </div>
  );
}
