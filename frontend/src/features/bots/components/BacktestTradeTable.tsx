import type { BacktestTrade } from '../../../services/backtests/types';

export function BacktestTradeTable({ trades }: { trades: BacktestTrade[] }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Side</th>
          <th>Qty</th>
          <th>Entry</th>
          <th>Exit</th>
          <th>Entry Slip</th>
          <th>Exit Slip</th>
          <th>Commission</th>
          <th>PnL</th>
          <th>Forced</th>
          <th>Opened</th>
          <th>Closed</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t, i) => (
          <tr key={i} data-forced={t.forced_close}>
            <td>{t.canonical_id}</td>
            <td>{t.side}</td>
            <td>{t.qty}</td>
            <td>{t.entry_price.toFixed(2)}</td>
            <td>{t.exit_price.toFixed(2)}</td>
            <td>{t.entry_slippage.toFixed(4)}</td>
            <td>{t.exit_slippage.toFixed(4)}</td>
            <td>{t.commission.toFixed(2)}</td>
            <td style={{ color: t.pnl >= 0 ? 'green' : 'red' }}>{t.pnl.toFixed(2)}</td>
            <td>{t.forced_close ? 'Yes' : ''}</td>
            <td>{new Date(t.opened_at).toLocaleDateString()}</td>
            <td>{new Date(t.closed_at).toLocaleDateString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
