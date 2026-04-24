import * as React from 'react';
import * as RadixRadio from '@radix-ui/react-radio-group';
import { cn } from '@/lib/utils';

export const RadioGroup = React.forwardRef<
  React.ElementRef<typeof RadixRadio.Root>,
  React.ComponentPropsWithoutRef<typeof RadixRadio.Root>
>(({ className, ...props }, ref) => (
  <RadixRadio.Root ref={ref} className={cn('grid gap-2', className)} {...props} />
));
RadioGroup.displayName = 'RadioGroup';

export const RadioItem = React.forwardRef<
  React.ElementRef<typeof RadixRadio.Item>,
  React.ComponentPropsWithoutRef<typeof RadixRadio.Item>
>(({ className, ...props }, ref) => (
  <RadixRadio.Item
    ref={ref}
    className={cn(
      'h-4 w-4 rounded-full border border-border bg-panel',
      'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-active',
      'disabled:cursor-not-allowed disabled:opacity-50',
      className,
    )}
    {...props}
  >
    <RadixRadio.Indicator className="flex items-center justify-center">
      <span className="block h-2 w-2 rounded-full bg-accent-active" />
    </RadixRadio.Indicator>
  </RadixRadio.Item>
));
RadioItem.displayName = 'RadioItem';
