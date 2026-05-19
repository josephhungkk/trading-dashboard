interface Props {
  curve: [string, number][];
}

export function BacktestPnlChart({ curve }: Props) {
  if (curve.length === 0) return null;
  return (
    <div aria-label="PnL curve" role="img">
      <svg width="100%" height="120" viewBox={`0 0 ${curve.length} 120`} preserveAspectRatio="none">
        <polyline
          fill="none"
          stroke="currentColor"
          strokeWidth="1"
          points={curve
            .map(([, v], i) => `${i},${60 - v}`)
            .join(' ')}
        />
      </svg>
    </div>
  );
}
