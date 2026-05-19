interface Props {
  curve: [string, number][];
}

export function BacktestDrawdownChart({ curve }: Props) {
  if (curve.length === 0) return null;
  return (
    <div aria-label="Drawdown curve" role="img">
      <svg width="100%" height="80" viewBox={`0 0 ${curve.length} 80`} preserveAspectRatio="none">
        <polyline
          fill="none"
          stroke="red"
          strokeWidth="1"
          points={curve
            .map(([, v], i) => `${i},${v}`)
            .join(' ')}
        />
      </svg>
    </div>
  );
}
