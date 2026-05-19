import { describe, it, expect } from 'vitest';
import type { BacktestReport } from './types';

describe('BacktestReport types', () => {
  it('accepts null sharpe', () => {
    const r: BacktestReport = {
      sharpe: null,
      mar: null,
      max_drawdown_pct: 0,
      total_return_pct: 0,
      total_trades: 0,
      win_rate: null,
      avg_trade_pnl: null,
      forced_close_pnl: 0,
      pnl_curve: [],
      drawdown_curve: [],
      trades: [],
    };
    expect(r.sharpe).toBeNull();
  });
});
