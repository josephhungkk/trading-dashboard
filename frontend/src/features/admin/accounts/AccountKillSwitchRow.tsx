/**
 * Phase 10a E5 — kill-switch toggle row for the /admin/accounts page.
 *
 * Off → on: opens a Dialog asking for a reason. On submit, calls the
 * setKillSwitch mutation. Cancel closes without mutating.
 * On → off: calls the mutation directly (spec: reason is required only
 * when ENABLING; the BE Pydantic validator rejects empty reason on
 * enable but allows empty reason on disable).
 */

import * as React from 'react';
import { useAccountKillSwitch } from '@/hooks/useAccountKillSwitch';
import { Button } from '@/components/primitives/Button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/primitives/Dialog';
import { Switch } from '@/components/primitives/Switch';

export interface AccountKillSwitchRowProps {
  accountId: string;
}

export function AccountKillSwitchRow({
  accountId,
}: AccountKillSwitchRowProps): React.JSX.Element {
  const { query, setKillSwitch } = useAccountKillSwitch(accountId);
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [reason, setReason] = React.useState('');

  if (query.isLoading) {
    return <span className="text-sm text-fg-muted">Loading…</span>;
  }

  const row = query.data ?? null;
  const isEnabled = row?.is_enabled === true;
  const enabledBy = row?.enabled_by ?? null;
  const isPending = setKillSwitch.isPending;

  const handleToggle = (next: boolean): void => {
    if (next) {
      setReason('');
      setDialogOpen(true);
    } else {
      setKillSwitch.mutate({ is_enabled: false, reason: '' });
    }
  };

  const handleSubmit = (): void => {
    const trimmed = reason.trim();
    if (!trimmed) return;
    setKillSwitch.mutate(
      { is_enabled: true, reason: trimmed },
      {
        onSuccess: () => {
          setDialogOpen(false);
          setReason('');
        },
      },
    );
  };

  return (
    <div className="flex items-center gap-3">
      <Switch
        checked={isEnabled}
        onCheckedChange={handleToggle}
        disabled={isPending}
        aria-label={`Account kill switch for ${accountId}`}
      />
      {isEnabled && enabledBy ? (
        <span className="text-xs text-fg-muted">frozen by {enabledBy}</span>
      ) : null}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Enable account kill switch</DialogTitle>
            <DialogDescription>
              Trading on this account will be blocked at the risk gate until
              the switch is disabled. Reason is recorded in the audit trail.
            </DialogDescription>
          </DialogHeader>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">Reason</span>
            <textarea
              value={reason}
              onChange={(event) => setReason(event.currentTarget.value)}
              maxLength={1000}
              required
              rows={3}
              className="w-full rounded-md border border-border bg-panel p-2 text-sm"
              placeholder="Operator note — visible in admin history"
            />
          </label>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setDialogOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              onClick={handleSubmit}
              disabled={isPending || reason.trim().length === 0}
            >
              {isPending ? 'Enabling…' : 'Enable'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
