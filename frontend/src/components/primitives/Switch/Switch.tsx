import * as React from 'react';
import * as RadixSwitch from '@radix-ui/react-switch';
import { cn } from '@/lib/utils';

export const Switch = React.forwardRef<
  React.ElementRef<typeof RadixSwitch.Root>,
  React.ComponentPropsWithoutRef<typeof RadixSwitch.Root>
>(({ className, ...props }, ref) => (
  <RadixSwitch.Root
    ref={ref}
    className={cn(
      'inline-flex h-6 w-11 shrink-0 items-center rounded-full border border-border bg-panel transition-colors',
      'data-[state=checked]:bg-accent-active focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-active',
      'disabled:cursor-not-allowed disabled:opacity-50',
      className,
    )}
    {...props}
  >
    <RadixSwitch.Thumb className="pointer-events-none block h-5 w-5 translate-x-0.5 rounded-full bg-fg transition-transform data-[state=checked]:translate-x-5" />
  </RadixSwitch.Root>
));
Switch.displayName = 'Switch';
