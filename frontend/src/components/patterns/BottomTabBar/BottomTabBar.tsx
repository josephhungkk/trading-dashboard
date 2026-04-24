import * as React from 'react';
import { Link } from '@tanstack/react-router';

interface TabDef {
  readonly to: '/overview' | '/orders' | '/positions' | '/watchlist' | '/more';
  readonly label: string;
}

const TABS: readonly TabDef[] = [
  { to: '/overview', label: 'Overview' },
  { to: '/orders', label: 'Orders' },
  { to: '/positions', label: 'Positions' },
  { to: '/watchlist', label: 'Watchlist' },
  { to: '/more', label: 'More' },
] as const;

const BASE_TAB_CLASS =
  'flex flex-1 flex-col items-center justify-center gap-1 min-h-[2.75rem] h-full text-xs transition-colors';
const ACTIVE_TAB_CLASS = 'text-primary font-medium';
const INACTIVE_TAB_CLASS = 'text-fg-muted hover:text-fg';

export function BottomTabBar(): React.JSX.Element {
  return (
    <nav
      // eslint-disable-next-line jsx-a11y/no-noninteractive-element-to-interactive-role -- tablist grouping is the intended pattern for this mobile tab bar
      role="tablist"
      aria-label="Primary navigation"
      className="fixed bottom-0 inset-x-0 z-40 flex h-14 border-t border-border bg-panel md:hidden"
    >
      {TABS.map((tab) => (
        <Link
          key={tab.to}
          to={tab.to}
          role="tab"
          className={`${BASE_TAB_CLASS} ${INACTIVE_TAB_CLASS}`}
          activeProps={{
            'aria-selected': true,
            className: `${BASE_TAB_CLASS} ${ACTIVE_TAB_CLASS}`,
          }}
          inactiveProps={{
            'aria-selected': false,
          }}
        >
          <span>{tab.label}</span>
        </Link>
      ))}
    </nav>
  );
}
