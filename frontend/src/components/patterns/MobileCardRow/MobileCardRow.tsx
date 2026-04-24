import * as React from 'react';
import { cn } from '@/lib/utils';

export interface MobileCardRowProps {
  primary: React.ReactNode;
  secondary?: React.ReactNode;
  metrics: { label: string; value: React.ReactNode }[];
  onClick?: () => void;
  className?: string;
}

export const MobileCardRow = React.memo(function MobileCardRow({
  primary,
  secondary,
  metrics,
  onClick,
  className,
}: MobileCardRowProps): React.JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'block w-full rounded-md border border-border bg-panel p-3 text-left',
        'min-h-[2.75rem]',
        className,
      )}
    >
      <div className="flex items-baseline justify-between">
        <span className="text-base font-semibold text-fg">{primary}</span>
      </div>
      {secondary && <div className="mt-0.5 text-xs text-fg-muted">{secondary}</div>}
      <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
        {metrics.map((m, i) => (
          <div key={`${m.label}-${i}`}>
            <span className="text-fg-muted">{m.label}:</span> {m.value}
          </div>
        ))}
      </div>
    </button>
  );
});
