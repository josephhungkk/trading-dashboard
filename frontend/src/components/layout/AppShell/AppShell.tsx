import * as React from 'react';
import { Outlet } from '@tanstack/react-router';
import { Topbar } from '@/components/layout/Topbar';
import { LeftPanel } from '@/components/layout/LeftPanel';
import { RightPanel } from '@/components/layout/RightPanel';
import { BottomTabBar } from '@/components/patterns/BottomTabBar';
import { CollapsibleDrawer } from '@/components/patterns/CollapsibleDrawer';
import { CommandPalette } from '@/components/patterns/CommandPalette';
import { ErrorBoundary } from '@/components/primitives/ErrorBoundary';
import { ResizablePanelFrame } from '@/components/patterns/ResizablePanelFrame';
// eslint-disable-next-line boundaries/element-types -- layout reads mode/scoped stores for hydration + body-attr
import { useModeStore } from '@/stores/global/mode';
// eslint-disable-next-line boundaries/element-types -- layout bootstraps scoped stores at shell mount
import { useActiveStores, getScopedStores } from '@/stores/registry';
// eslint-disable-next-line boundaries/element-types -- layout passes service registry to store hydrate
import { getServices } from '@/services/registry';

/**
 * AppShell — single-subtree responsive layout.
 *
 * Desktop (md and up): three-panel horizontal layout with resizable
 * left/right panels and the main route outlet between them.
 *
 * Mobile (below md): single-column outlet with the left and right panels
 * available as off-canvas drawers. A bottom tab bar is always mounted
 * below md (the bar itself carries the `md:hidden` class).
 *
 * On mount, hydrates the active scoped stores from the service registry
 * and reflects the active mode on `<body data-mode>` so theme tokens can
 * key off it. On unmount, suspends the scoped stores for the mode that
 * was active during the effect.
 */
export function AppShell(): React.JSX.Element {
  const mode = useModeStore((s) => s.mode);
  const stores = useActiveStores();

  React.useEffect(() => {
    void stores.hydrate(getServices());
    return () => {
      getScopedStores(mode).suspend();
    };
  }, [mode, stores]);

  React.useEffect(() => {
    document.body.setAttribute('data-mode', mode);
    return () => {
      document.body.removeAttribute('data-mode');
    };
  }, [mode]);

  const [leftOpen, setLeftOpen] = React.useState(false);
  const [rightOpen, setRightOpen] = React.useState(false);

  return (
    <ErrorBoundary>
      <div className="flex h-screen flex-col">
        <Topbar />
        <div className="flex-1 overflow-hidden">
          {/* Desktop: 3-panel horizontal resize */}
          <div className="hidden h-full md:block">
            <ResizablePanelFrame
              direction="horizontal"
              autoSaveId="shell-horizontal-desktop"
              panels={[
                { id: 'left', defaultSize: 20, minSize: 15, collapsible: true, content: <LeftPanel /> },
                {
                  id: 'main',
                  defaultSize: 60,
                  minSize: 30,
                  content: (
                    <main className="h-full overflow-auto">
                      <Outlet />
                    </main>
                  ),
                },
                { id: 'right', defaultSize: 20, minSize: 15, collapsible: true, content: <RightPanel /> },
              ]}
            />
          </div>
          {/* Mobile: single column + drawers */}
          <div className="block h-full md:hidden">
            <main className="h-full overflow-auto pb-16">
              <Outlet />
            </main>
            <CollapsibleDrawer open={leftOpen} onOpenChange={setLeftOpen} side="left">
              <LeftPanel />
            </CollapsibleDrawer>
            <CollapsibleDrawer open={rightOpen} onOpenChange={setRightOpen} side="right">
              <RightPanel />
            </CollapsibleDrawer>
          </div>
        </div>
        <BottomTabBar />
      </div>
      <CommandPalette />
    </ErrorBoundary>
  );
}
