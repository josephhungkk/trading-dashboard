// Reference:
// - TradingView Pine Script ta.linreg built-in
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
// - Least squares regression cross-reference
//   https://en.wikipedia.org/wiki/Linear_regression
//
// Notes: Projects the least-squares regression line one bar beyond the current window.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const tsfIndicator = createCustomIndicator({
  name: 'TSF',
  shortName: 'TSF',
  series: 'price',
  precision: 2,
  calcParams: [14],
  shouldOhlc: true,
  figures: [
    { key: 'tsf', title: 'TSF: ', type: 'line' },
  ],
  calc: indicatorCalcs.tsf,
});
