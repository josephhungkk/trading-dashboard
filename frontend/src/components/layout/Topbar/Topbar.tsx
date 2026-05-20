import * as React from 'react';
import { Link } from '@tanstack/react-router';
import { Search } from 'lucide-react';
import { ModeToggle } from '@/components/patterns/ModeToggle';
import { AccountPicker } from '@/features/accounts/AccountPicker';
import { BellDropdown } from '@/features/alerts/BellDropdown';
import { ConnectedDropdown } from '@/components/patterns/ConnectedDropdown';
import { Button } from '@/components/primitives/Button';
import { Icon } from '@/components/primitives/Icon';
// eslint-disable-next-line boundaries/element-types -- command palette trigger dispatches into global commands store (intentional cross-layer)
import { useCommandsStore } from '@/stores/global/commands';

interface RouteEntry {
  readonly to: string;
  readonly label: string;
}

const ROUTES: readonly RouteEntry[] = [
  { to: '/overview', label: 'Overview' },
  { to: '/orders', label: 'Orders' },
  { to: '/positions', label: 'Positions' },
  { to: '/watchlist', label: 'Watchlist' },
  { to: '/orchestration', label: 'Orchestration' },
  { to: '/admin', label: 'Admin' },
  { to: '/settings', label: 'Settings' },
] as const;

export function Topbar(): React.JSX.Element {
  const openPalette = useCommandsStore((s) => s.setOpen);

  function handleOpenPalette(): void {
    openPalette(true);
  }

  return (
    <header className="relative flex flex-col gap-2 border-b border-border bg-panel px-4 py-2 md:flex-row md:items-center md:justify-between">
      <div
        aria-hidden
        className="pointer-events-none absolute bottom-0 left-0 right-0 h-0.5 bg-accent-active"
      />
      <div className="flex items-center gap-4">
        <strong className="text-base">Trading Dashboard</strong>
        <ModeToggle />
        <AccountPicker />
        <ConnectedDropdown />
      </div>
      <div className="flex items-center gap-2">
        <nav aria-label="Primary" className="hidden items-center gap-1 md:flex">
          {ROUTES.map((r) => (
            // Cast: story/test routers don't register the same typed route tree;
            // the real app tree accepts these paths at runtime.
            <Link
              key={r.to}
              to={r.to as never}
              className="rounded px-3 py-1 text-sm text-fg-muted hover:bg-muted/10"
            >
              {r.label}
            </Link>
          ))}
        </nav>
        <Button variant="ghost" onClick={handleOpenPalette} aria-label="Open command palette">
          <Icon as={Search} size="sm" />
          <span>⌘K</span>
        </Button>
        <BellDropdown />
      </div>
    </header>
  );
}
