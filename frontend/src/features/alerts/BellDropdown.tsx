import * as React from 'react';
import { useState } from 'react';

import { useAlertsFeed } from '@/hooks/useAlertsFeed';
import { useAlertsStore } from '@/stores/global/alerts';

export function BellDropdown(): React.JSX.Element {
  useAlertsFeed();
  const fires = useAlertsStore((s) => s.recentFires);
  const [open, setOpen] = useState(false);

  return (
    <div className="relative" data-testid="bell-dropdown">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={`Alerts: ${fires.length} recent fires`}
        className="relative rounded-md p-2 hover:bg-muted"
        data-testid="bell-toggle"
      >
        <span aria-hidden>🔔</span>
        {fires.length > 0 && (
          <span
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-red-500 px-1 text-[0.625rem] font-medium text-white"
            data-testid="bell-badge"
          >
            {fires.length > 99 ? '99+' : fires.length}
          </span>
        )}
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 mt-2 w-80 rounded-md border border-border bg-background p-2 shadow-lg"
          data-testid="bell-menu"
        >
          <h2 className="px-2 py-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Recent fires
          </h2>
          {fires.length === 0 ? (
            <p className="px-2 py-3 text-xs text-muted-foreground">
              No fires yet
            </p>
          ) : (
            <ul className="max-h-80 space-y-0.5 overflow-y-auto">
              {fires.slice(0, 20).map((fire) => (
                <li key={fire.id}>
                  <a
                    href={`/alerts/${fire.alert_id}`}
                    className="flex items-center justify-between rounded-md px-2 py-1.5 text-xs hover:bg-muted"
                    data-testid={`bell-fire-${fire.id}`}
                  >
                    <span className="font-mono">#{fire.alert_id}</span>
                    <span className="text-muted-foreground">{fire.verdict}</span>
                    <span className="text-muted-foreground">{fire.fired_at}</span>
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
