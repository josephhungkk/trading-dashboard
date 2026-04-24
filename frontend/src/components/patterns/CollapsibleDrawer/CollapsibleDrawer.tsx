import * as React from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface CollapsibleDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  side?: 'left' | 'right';
  children: React.ReactNode;
  title?: string;
  widthClass?: string;
}

/**
 * CollapsibleDrawer — mobile-first slide-in panel.
 *
 * Thin wrapper around Radix Dialog that slides in from the left or right
 * edge of the viewport (instead of the centered modal layout of the base
 * Dialog primitive). Primarily intended for mobile navigation drawers.
 *
 * A visually-hidden Dialog.Title is always rendered so Radix's a11y
 * requirement is satisfied without forcing consumers to supply a visible
 * heading.
 */
export function CollapsibleDrawer({
  open,
  onOpenChange,
  side = 'left',
  children,
  title = 'Drawer',
  widthClass = 'w-72',
}: CollapsibleDrawerProps): React.JSX.Element {
  const slideClass =
    side === 'left'
      ? 'left-0 border-r data-[state=closed]:-translate-x-full data-[state=open]:translate-x-0'
      : 'right-0 border-l data-[state=closed]:translate-x-full data-[state=open]:translate-x-0';

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          data-testid="collapsible-drawer-overlay"
          className={cn(
            'fixed inset-0 z-40 bg-black/60',
            'data-[state=closed]:animate-out data-[state=open]:animate-in',
          )}
        />
        <DialogPrimitive.Content
          data-testid="collapsible-drawer-content"
          className={cn(
            'fixed top-0 z-50 flex h-dvh flex-col',
            'bg-panel border-border',
            'transition-transform duration-base',
            widthClass,
            slideClass,
          )}
        >
          <DialogPrimitive.Title className="sr-only">{title}</DialogPrimitive.Title>
          <DialogPrimitive.Close
            className={cn(
              'absolute right-3 top-3 rounded-sm text-fg-muted',
              'transition-opacity hover:text-fg focus:outline-none',
              'focus:ring-1 focus:ring-accent-active',
            )}
            aria-label="Close"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </DialogPrimitive.Close>
          <div className="flex-1 overflow-y-auto">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
