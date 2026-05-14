import * as React from 'react';
import { Link, Outlet } from '@tanstack/react-router';
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
// eslint-disable-next-line boundaries/element-types -- layout initialises global stores lazily on mount (HIGH-3)
import { useConnectedStore } from '@/stores/global/connected';
// eslint-disable-next-line boundaries/element-types -- layout initialises global stores lazily on mount (HIGH-3)
import { useCommandsStore } from '@/stores/global/commands';
// eslint-disable-next-line boundaries/element-types -- hooks layer composes account fetch with maintenance publish
import { fetchAccountsAndSyncMaintenance } from '@/hooks/useAccountsList';

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

  // Initialise global stores lazily so getServices() is not called at module-eval time (HIGH-3)
  React.useEffect(() => {
    useConnectedStore.getState().init();
    useCommandsStore.getState().init();
  }, []);

  React.useEffect(() => {
    const capturedMode = mode;
    void stores.hydrate(getServices(), fetchAccountsAndSyncMaintenance);
    return () => {
      getScopedStores(capturedMode).suspend();
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
        {/* Options secondary nav — desktop only; hidden on mobile (covered by BottomTabBar → More) */}
        <nav
          aria-label="Options navigation"
          className="hidden border-t border-border bg-panel px-4 py-2 md:flex md:gap-4"
        >
          <Link
            to="/options/chain"
            search={{ symbol: undefined, expiry: undefined }}
            className="text-sm text-fg-muted transition-colors hover:text-fg"
            activeProps={{ className: 'text-sm text-primary font-medium' }}
          >
            Option Chain
          </Link>
          <Link
            to="/options/events"
            className="text-sm text-fg-muted transition-colors hover:text-fg"
            activeProps={{ className: 'text-sm text-primary font-medium' }}
          >
            Exercise Events
          </Link>
        </nav>
      </div>
      <CommandPalette />
    </ErrorBoundary>
  );
}
