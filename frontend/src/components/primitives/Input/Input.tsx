import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const inputVariants = cva(
  'h-10 w-full rounded-md border border-border bg-panel px-3 text-sm text-fg placeholder:text-fg-subtle focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-active disabled:opacity-50',
  {
    variants: {
      variant: {
        default: '',
        numeric: 'text-right font-mono tabular-nums',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

export type InputProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> &
  VariantProps<typeof inputVariants>;

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, variant, type = 'text', ...props }, ref) => (
    <input ref={ref} type={type} className={cn(inputVariants({ variant, className }))} {...props} />
  ),
);
Input.displayName = 'Input';
