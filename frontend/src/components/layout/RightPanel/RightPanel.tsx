import * as React from 'react';
import { ResizablePanelFrame } from '@/components/patterns/ResizablePanelFrame';
import { OpenOrdersCompact } from '@/features/orders/OpenOrdersCompact';
import { PositionsCompact } from '@/features/positions/PositionsCompact';

export function RightPanel(): React.JSX.Element {
  return (
    <ResizablePanelFrame
      direction="vertical"
      autoSaveId="shell-right-desktop"
      panels={[
        { id: 'orders', defaultSize: 40, minSize: 20, content: <OpenOrdersCompact /> },
        { id: 'positions', defaultSize: 60, minSize: 30, content: <PositionsCompact /> },
      ]}
    />
  );
}
