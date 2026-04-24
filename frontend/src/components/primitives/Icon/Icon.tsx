import * as React from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

export type IconSize = 'sm' | 'md' | 'lg';

export interface IconProps {
  as: LucideIcon;
  size?: IconSize;
  className?: string;
  'aria-label'?: string;
  'aria-hidden'?: boolean;
}

const SIZE_CLASSES: Record<IconSize, string> = {
  sm: 'h-4 w-4',
  md: 'h-5 w-5',
  lg: 'h-6 w-6',
};

export function Icon({
  as: Component,
  size = 'md',
  className,
  'aria-label': ariaLabel,
  'aria-hidden': ariaHidden,
}: IconProps): React.JSX.Element {
  const hidden = ariaHidden ?? !ariaLabel;
  return (
    <Component
      className={cn(SIZE_CLASSES[size], className)}
      aria-label={ariaLabel}
      aria-hidden={hidden || undefined}
      role={ariaLabel ? 'img' : undefined}
    />
  );
}
