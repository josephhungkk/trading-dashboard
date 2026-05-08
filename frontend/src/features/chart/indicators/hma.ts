// Reference:
// - Alan Hull, Hull Moving Average
//   https://alanhull.com/hull-moving-average
// - TradingView Pine Script ta.wma built-in cross-reference
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: Uses WMA(2 * WMA(period / 2) - WMA(period), floor(sqrt(period))).

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const hmaIndicator = createCustomIndicator({
  name: 'HMA',
  shortName: 'HMA',
  series: 'price',
  precision: 2,
  calcParams: [20],
  shouldOhlc: true,
  figures: [
    { key: 'hma', title: 'HMA: ', type: 'line' },
  ],
  calc: indicatorCalcs.hma,
});
