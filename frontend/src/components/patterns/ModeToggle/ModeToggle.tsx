import * as React from 'react';
// eslint-disable-next-line boundaries/element-types -- modetoggle needs reactive mode store + dispatch actions
import { useModeStore } from '@/stores/global/mode';
import { Switch } from '@/components/primitives/Switch';
import { Badge } from '@/components/primitives/Badge';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from '@/components/primitives/Dialog';
import { Button } from '@/components/primitives/Button';
// eslint-disable-next-line boundaries/element-types -- toast dispatch from mode switcher
import { useToast } from '@/hooks/use-toast';
// eslint-disable-next-line boundaries/element-types -- scoped store lifecycle via registry (H6 hydrate/suspend)
import { getScopedStores } from '@/stores/registry';
// eslint-disable-next-line boundaries/element-types -- services singleton for hydration
import { getServices } from '@/services/registry';

export function ModeToggle(): React.JSX.Element {
  const mode = useModeStore((s) => s.mode);
  const pendingMode = useModeStore((s) => s.pendingMode);
  const status = useModeStore((s) => s.status);
  const requestModeSwitch = useModeStore((s) => s.requestModeSwitch);
  const cancelModeSwitch = useModeStore((s) => s.cancelModeSwitch);
  const setStatus = useModeStore((s) => s.setStatus);
  const setMode = useModeStore((s) => s.setMode);
  const { toast } = useToast();

  async function performSwitch(target: 'live' | 'paper'): Promise<void> {
    setStatus('switching');
    const from = mode;
    try {
      await getScopedStores(target).hydrate(getServices());
      getScopedStores(from).suspend();
      setMode(target);
      toast({ title: `Switched to ${target.toUpperCase()} mode`, tone: 'success' });
    } finally {
      setStatus('idle');
    }
  }

  function handleCheckedChange(next: boolean): void {
    if (next) requestModeSwitch('live');
    else void performSwitch('paper');
  }

  function handleConfirmLive(): void {
    cancelModeSwitch();
    void performSwitch('live');
  }

  return (
    <>
      <div className="flex items-center gap-2" data-testid="mode-toggle">
        <Badge variant={mode}>{mode.toUpperCase()}</Badge>
        <Switch
          aria-label="mode"
          checked={mode === 'live'}
          disabled={status === 'switching'}
          onCheckedChange={handleCheckedChange}
        />
      </div>
      <Dialog
        open={pendingMode === 'live'}
        onOpenChange={(open) => {
          if (!open) cancelModeSwitch();
        }}
      >
        <DialogContent>
          <DialogTitle>Switch to LIVE mode?</DialogTitle>
          <DialogDescription>
            Real accounts and real orders will appear. Any action from here on may affect real
            money.
          </DialogDescription>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button variant="destructive" onClick={handleConfirmLive}>
              Continue to LIVE
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
