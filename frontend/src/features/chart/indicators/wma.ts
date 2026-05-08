// Reference:
// - TradingView Pine Script ta.wma built-in
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
// - klinecharts moving average implementation cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Uses linear weights 1..n, with the newest bar receiving weight n.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const wmaIndicator = createCustomIndicator({
  name: 'WMA',
  shortName: 'WMA',
  series: 'price',
  precision: 2,
  calcParams: [20],
  shouldOhlc: true,
  figures: [
    { key: 'wma', title: 'WMA: ', type: 'line' },
  ],
  calc: indicatorCalcs.wma,
});
