// Reference:
// - TradingView Pine Script ta.linreg built-in
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
// - Least squares regression cross-reference
//   https://en.wikipedia.org/wiki/Linear_regression
//
// Notes: Least-squares line endpoint over the lookback window.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const lsmaIndicator = createCustomIndicator({
  name: 'LSMA',
  shortName: 'LSMA',
  series: 'price',
  precision: 2,
  calcParams: [25],
  shouldOhlc: true,
  figures: [
    { key: 'lsma', title: 'LSMA: ', type: 'line' },
  ],
  calc: indicatorCalcs.lsma,
});
