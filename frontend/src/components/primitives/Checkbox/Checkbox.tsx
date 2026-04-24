import * as React from 'react';
import * as RadixCheckbox from '@radix-ui/react-checkbox';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';

export const Checkbox = React.forwardRef<
  React.ElementRef<typeof RadixCheckbox.Root>,
  React.ComponentPropsWithoutRef<typeof RadixCheckbox.Root>
>(({ className, ...props }, ref) => (
  <RadixCheckbox.Root
    ref={ref}
    className={cn(
      'peer h-4 w-4 shrink-0 rounded-sm border border-border bg-panel',
      'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-active',
      'data-[state=checked]:bg-accent-active data-[state=checked]:border-accent-active',
      'disabled:cursor-not-allowed disabled:opacity-50',
      className,
    )}
    {...props}
  >
    <RadixCheckbox.Indicator className="flex items-center justify-center text-primary-fg">
      <Check className="h-3.5 w-3.5" strokeWidth={3} />
    </RadixCheckbox.Indicator>
  </RadixCheckbox.Root>
));
Checkbox.displayName = 'Checkbox';
