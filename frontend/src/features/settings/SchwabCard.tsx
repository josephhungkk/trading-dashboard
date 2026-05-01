import * as React from 'react';
import { useState } from 'react';

import { Button } from '@/components/primitives/Button';
import { Switch } from '@/components/primitives/Switch';
import { useSchwabTokenStatus } from '@/hooks/useSchwabTokenStatus';
import { connectStart, disconnect, enableTier2 } from '@/services/schwab';

const REFRESH_TOKEN_TTL_HOURS = 168;
const WARN_AT_HOURS = 144;
const HOURS_PER_MS = 1 / 3_600_000;

type CardState = 'ok' | 'warn' | 'expired';

function formatDuration(hours: number): string {
  const days = Math.floor(hours / 24);
  const h = Math.floor(hours % 24);
  return days > 0 ? `${days}d ${h}h` : `${h}h`;
}

export function SchwabCard(): React.JSX.Element {
  const { status, loading, refetch, startFastPoll } = useSchwabTokenStatus();
  const [showDisconnect, setShowDisconnect] = useState(false);
  const [deleteCreds, setDeleteCreds] = useState(false);
  const [nowMs] = useState(() => Date.now());

  if (loading) {
    return (
      <section aria-busy="true">
        <h3>Schwab</h3>
        <p>Loading…</p>
      </section>
    );
  }

  const connected = !!status?.refreshTokenIssuedAt;
  const ageHours = status?.refreshTokenIssuedAt
    ? (nowMs - status.refreshTokenIssuedAt.getTime()) * HOURS_PER_MS
    : Infinity;
  const expiresInHours = Math.max(0, REFRESH_TOKEN_TTL_HOURS - ageHours);
  const state: CardState =
    ageHours > REFRESH_TOKEN_TTL_HOURS ? 'expired' : ageHours > WARN_AT_HOURS ? 'warn' : 'ok';

  const handleConnect = (): void => {
    connectStart();
    startFastPoll();
  };

  const handleTier2 = (checked: boolean): void => {
    void (async (): Promise<void> => {
      await enableTier2(undefined, checked);
      void refetch();
    })();
  };

  const handleDisconnect = (): void => {
    void (async (): Promise<void> => {
      await disconnect(undefined, { deleteCredentials: deleteCreds });
      setShowDisconnect(false);
      void refetch();
    })();
  };

  return (
    <section>
      <h3>Schwab</h3>
      {connected ? (
        <>
          <p>● Connected</p>
          <p data-testid="expiring-badge" data-state={state}>
            Refresh token{' '}
            {state === 'expired' ? 'EXPIRED' : `expires in ${formatDuration(expiresInHours)}`}
          </p>
        </>
      ) : (
        <p>Not connected</p>
      )}

      <div>
        <Button onClick={handleConnect}>
          {connected ? 'Re-authorize now' : 'Connect Schwab'}
        </Button>
        {connected ? (
          <Button
            variant="ghost"
            onClick={() => {
              setShowDisconnect(true);
            }}
          >
            Disconnect
          </Button>
        ) : null}
      </div>

      {connected ? (
        <label>
          <Switch
            checked={!!status?.tier2RefreshEnabled}
            onCheckedChange={handleTier2}
          />
          Enable Tier-2 auto-refresh (Playwright; every 3 days)
          {status && status.tier2ConsecutiveFailures >= 1 ? (
            <span data-testid="tier2-failures">
              {status.tier2ConsecutiveFailures} consecutive failures
            </span>
          ) : null}
        </label>
      ) : null}

      {showDisconnect ? (
        <div role="dialog" aria-label="Disconnect Schwab">
          <h4>Disconnect Schwab?</h4>
          <p>This will sign out the dashboard from Schwab and stop quoting / trading.</p>
          {status?.tier2RefreshEnabled ? (
            <label>
              <input
                type="checkbox"
                checked={deleteCreds}
                onChange={(e) => {
                  setDeleteCreds(e.target.checked);
                }}
              />
              Also delete saved credentials (username/password/TOTP)
            </label>
          ) : null}
          <div>
            <Button
              variant="ghost"
              onClick={() => {
                setShowDisconnect(false);
              }}
            >
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDisconnect}>
              Disconnect
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
