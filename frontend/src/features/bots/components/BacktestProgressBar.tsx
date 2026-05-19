interface Props {
  pct: number;
  tradesSoFar: number;
  currentBarTs: string;
  onCancel: () => void;
}

export function BacktestProgressBar({ pct, tradesSoFar, currentBarTs, onCancel }: Props) {
  return (
    <div aria-label="Backtest progress">
      <progress value={pct} max={100} aria-valuenow={pct} aria-label={`${pct}% complete`} />
      <span>{pct}%</span>
      <span>Bar: {currentBarTs ? new Date(currentBarTs).toLocaleDateString() : '—'}</span>
      <span>Trades so far: {tradesSoFar}</span>
      <button onClick={onCancel}>Cancel</button>
    </div>
  );
}
