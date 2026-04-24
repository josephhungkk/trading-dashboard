import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium',
  {
    variants: {
      variant: {
        neutral: 'border-border bg-panel text-fg-muted',
        live: 'border-transparent bg-accent-live text-primary-fg',
        paper: 'border-transparent bg-accent-paper text-bg',
        delayed: 'border-transparent bg-delayed-bg text-delayed-fg',
        up: 'border-transparent bg-positive/15 text-positive',
        down: 'border-transparent bg-negative/15 text-negative',
        warn: 'border-transparent bg-warn/15 text-warn',
      },
    },
    defaultVariants: { variant: 'neutral' },
  },
);

export type BadgeProps = React.HTMLAttributes<HTMLSpanElement> &
  VariantProps<typeof badgeVariants>;

export function Badge({ className, variant, ...props }: BadgeProps): React.JSX.Element {
  return <span className={cn(badgeVariants({ variant, className }))} {...props} />;
}
