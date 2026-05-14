import type { GreeksSnapshot } from './types';

interface Props {
  greeks: Partial<GreeksSnapshot>;
  className?: string;
}

function fmt(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return v.toFixed(3);
}

export function OptionGreeksStrip({ greeks, className }: Props) {
  return (
    <div className={`flex gap-4 text-xs ${className ?? ''}`} data-testid="greeks-strip">
      <span>
        <span className="text-muted-foreground">Δ</span> {fmt(greeks.delta)}
      </span>
      <span>
        <span className="text-muted-foreground">Γ</span> {fmt(greeks.gamma)}
      </span>
      <span>
        <span className="text-muted-foreground">Θ</span> {fmt(greeks.theta)}
      </span>
      <span>
        <span className="text-muted-foreground">V</span> {fmt(greeks.vega)}
      </span>
      <span>
        <span className="text-muted-foreground">IV</span>{' '}
        {greeks.iv !== null && greeks.iv !== undefined
          ? `${(greeks.iv * 100).toFixed(1)}%`
          : '—'}
      </span>
    </div>
  );
}
