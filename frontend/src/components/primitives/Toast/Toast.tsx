import * as React from 'react';
import * as ToastPrimitive from '@radix-ui/react-toast';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';
// eslint-disable-next-line boundaries/element-types -- toast queue is a cross-layer leaf hook for toast orchestration
import { useToastStore } from '@/hooks/use-toast';

export const ToastProvider = ToastPrimitive.Provider;

export const ToastViewport = React.forwardRef<
  React.ElementRef<typeof ToastPrimitive.Viewport>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitive.Viewport>
>(({ className, ...props }, ref) => (
  <ToastPrimitive.Viewport
    ref={ref}
    className={cn(
      'fixed bottom-0 right-0 z-50 flex max-h-screen w-full flex-col-reverse gap-2 p-4 sm:max-w-sm',
      className,
    )}
    {...props}
  />
));
ToastViewport.displayName = 'ToastViewport';

const TONE_CLASSES = {
  neutral: 'border-border bg-panel text-fg',
  success: 'border-positive/30 bg-panel text-fg',
  error: 'border-destructive/50 bg-panel text-fg',
} as const;

export type ToastTone = keyof typeof TONE_CLASSES;

export const Toast = React.forwardRef<
  React.ElementRef<typeof ToastPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitive.Root> & { tone?: ToastTone }
>(({ className, tone = 'neutral', ...props }, ref) => (
  <ToastPrimitive.Root
    ref={ref}
    className={cn(
      'relative flex w-full items-center justify-between gap-2 overflow-hidden rounded-md border p-4 shadow-lg transition-opacity duration-150',
      TONE_CLASSES[tone],
      className,
    )}
    {...props}
  />
));
Toast.displayName = 'Toast';

export const ToastTitle = React.forwardRef<
  React.ElementRef<typeof ToastPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitive.Title>
>(({ className, ...props }, ref) => (
  <ToastPrimitive.Title
    ref={ref}
    className={cn('text-sm font-semibold', className)}
    {...props}
  />
));
ToastTitle.displayName = 'ToastTitle';

export const ToastDescription = React.forwardRef<
  React.ElementRef<typeof ToastPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitive.Description>
>(({ className, ...props }, ref) => (
  <ToastPrimitive.Description
    ref={ref}
    className={cn('text-sm text-fg-muted', className)}
    {...props}
  />
));
ToastDescription.displayName = 'ToastDescription';

export const ToastClose = React.forwardRef<
  React.ElementRef<typeof ToastPrimitive.Close>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitive.Close>
>(({ className, ...props }, ref) => (
  <ToastPrimitive.Close
    ref={ref}
    className={cn(
      'rounded-sm opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-accent-active',
      className,
    )}
    aria-label="Close"
    {...props}
  >
    <X className="h-4 w-4" aria-hidden="true" />
  </ToastPrimitive.Close>
));
ToastClose.displayName = 'ToastClose';

export const ToastAction = React.forwardRef<
  React.ElementRef<typeof ToastPrimitive.Action>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitive.Action>
>(({ className, ...props }, ref) => (
  <ToastPrimitive.Action
    ref={ref}
    className={cn(
      'inline-flex h-8 items-center justify-center rounded-md border border-border bg-transparent px-3 text-sm transition-colors hover:bg-muted/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-active',
      className,
    )}
    {...props}
  />
));
ToastAction.displayName = 'ToastAction';

export function Toaster(): React.JSX.Element {
  const items = useToastStore((s) => s.items);
  const dismiss = useToastStore((s) => s.dismiss);
  return (
    <ToastProvider>
      {items.map((t) => (
        <Toast
          key={t.id}
          tone={t.tone ?? 'neutral'}
          onOpenChange={(open) => {
            if (!open) dismiss(t.id);
          }}
        >
          <div className="flex flex-col gap-1">
            {t.title !== undefined && t.title !== '' ? (
              <ToastTitle>{t.title}</ToastTitle>
            ) : null}
            {t.description !== undefined && t.description !== '' ? (
              <ToastDescription>{t.description}</ToastDescription>
            ) : null}
          </div>
          <ToastClose />
        </Toast>
      ))}
      <ToastViewport />
    </ToastProvider>
  );
}
