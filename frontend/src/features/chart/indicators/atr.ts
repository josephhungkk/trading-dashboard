// Reference:
// - J. Welles Wilder Jr., 1978, Average True Range
//   https://en.wikipedia.org/wiki/Average_true_range
// - TradingView Pine Script ta.atr built-in
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: Uses Wilder's RMA smoothing of true range.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const atrIndicator = createCustomIndicator({
  name: 'ATR',
  shortName: 'ATR',
  series: 'normal',
  precision: 2,
  calcParams: [14],
  shouldOhlc: true,
  figures: [
    { key: 'atr', title: 'ATR: ', type: 'line' },
  ],
  calc: indicatorCalcs.atr,
});
