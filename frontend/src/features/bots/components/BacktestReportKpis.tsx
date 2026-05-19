import type { BacktestReport } from '../../../services/backtests/types';

function fmt(v: number | null, digits = 2): string {
  return v === null ? '—' : v.toFixed(digits);
}

export function BacktestReportKpis({ report }: { report: BacktestReport }) {
  return (
    <div role="region" aria-label="Backtest KPIs">
      <dl>
        <dt>Sharpe</dt>
        <dd>{fmt(report.sharpe, 2)}</dd>
        <dt>MAR</dt>
        <dd>{fmt(report.mar, 2)}</dd>
        <dt>Max Drawdown</dt>
        <dd>{fmt(report.max_drawdown_pct, 2)}%</dd>
        <dt>Total Return</dt>
        <dd>{fmt(report.total_return_pct, 2)}%</dd>
        <dt>Trades</dt>
        <dd>{report.total_trades}</dd>
        <dt>Win Rate</dt>
        <dd>{report.win_rate !== null ? `${(report.win_rate * 100).toFixed(1)}%` : '—'}</dd>
      </dl>
      {report.forced_close_pnl !== 0 && (
        <p role="note" style={{ color: 'orange' }}>
          Includes {report.forced_close_pnl > 0 ? '+' : ''}
          {report.forced_close_pnl.toFixed(2)} from forced end-of-range closes
        </p>
      )}
    </div>
  );
}
