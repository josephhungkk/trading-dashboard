// Reference:
// - TradingView Pine Script ta.vwma built-in
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Default period is 20; formula uses sum(close * volume, n) / sum(volume, n).

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const vwmaIndicator = createCustomIndicator({
  name: 'VWMA',
  shortName: 'VWMA',
  series: 'price',
  precision: 2,
  calcParams: [20],
  shouldOhlc: true,
  figures: [
    { key: 'vwma', title: 'VWMA: ', type: 'line' },
  ],
  calc: indicatorCalcs.vwma,
});
