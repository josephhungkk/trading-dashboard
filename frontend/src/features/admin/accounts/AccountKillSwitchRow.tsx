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
  /** Optional human-readable label for AT (e.g. account alias); falls
   * back to the UUID so multi-row pages stay distinguishable. */
  accountLabel?: string;
}

export function AccountKillSwitchRow({
  accountId,
  accountLabel,
}: AccountKillSwitchRowProps): React.JSX.Element {
  const { query, setKillSwitch } = useAccountKillSwitch(accountId);
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [reason, setReason] = React.useState('');
  // E7-fix: per-row mutation error displayed inline so the operator
  // can tell an enable failure from a slow network.
  const mutationError = setKillSwitch.error;

  if (query.isLoading) {
    return <span className="text-sm text-fg-muted">Loading…</span>;
  }

  // E7-fix (code-quality MED): explicit error branch — without it the
  // Switch renders in the off state over a failed fetch with no
  // indication the read failed.
  if (query.error) {
    return (
      <span role="alert" className="text-sm text-destructive">
        Failed to load kill-switch state
      </span>
    );
  }

  const row = query.data ?? null;
  const isEnabled = row?.is_enabled === true;
  const enabledBy = row?.enabled_by ?? null;
  const isPending = setKillSwitch.isPending;
  const switchLabel = accountLabel ?? accountId;
  const textareaId = `kill-switch-reason-${accountId}`;

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
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-3">
        <Switch
          checked={isEnabled}
          onCheckedChange={handleToggle}
          disabled={isPending}
          aria-label={`Account kill switch for ${switchLabel}`}
        />
        {isEnabled && enabledBy ? (
          <span className="text-xs text-fg-muted">frozen by {enabledBy}</span>
        ) : null}
      </div>
      {mutationError ? (
        <span role="alert" className="text-xs text-destructive">
          {mutationError.message}
        </span>
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
          <label htmlFor={textareaId} className="flex flex-col gap-1 text-sm">
            <span className="font-medium">Reason</span>
            <textarea
              id={textareaId}
              value={reason}
              onChange={(event) => setReason(event.currentTarget.value)}
              maxLength={1000}
              required
              rows={3}
              className="w-full rounded-md border border-border bg-panel p-2 text-sm"
              placeholder="Operator note — visible in admin history"
            />
          </label>
          {mutationError ? (
            <p role="alert" className="text-sm text-destructive">
              {mutationError.message}
            </p>
          ) : null}
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
