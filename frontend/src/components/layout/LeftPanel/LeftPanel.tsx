import * as React from 'react';
import { ResizablePanelFrame } from '@/components/patterns/ResizablePanelFrame';
import { AccountSummary } from '@/features/overview/AccountSummary';
import { WatchlistCompact } from '@/features/watchlist/WatchlistCompact';

export function LeftPanel(): React.JSX.Element {
  return (
    <ResizablePanelFrame
      direction="vertical"
      autoSaveId="shell-left-desktop"
      panels={[
        { id: 'summary', defaultSize: 40, minSize: 20, content: <AccountSummary /> },
        { id: 'watchlist', defaultSize: 60, minSize: 30, content: <WatchlistCompact /> },
      ]}
    />
  );
}
