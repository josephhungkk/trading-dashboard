// Reference:
// - TradingView Pine Script ta.vwap built-in
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
// - Volume-weighted average price cross-reference
//   https://en.wikipedia.org/wiki/Volume-weighted_average_price
//
// Notes: Session anchor is the loaded data window; formula uses cumulative typical price * volume / cumulative volume.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const vwapIndicator = createCustomIndicator({
  name: 'VWAP',
  shortName: 'VWAP',
  series: 'price',
  precision: 2,
  calcParams: [],
  shouldOhlc: true,
  figures: [
    { key: 'vwap', title: 'VWAP: ', type: 'line' },
  ],
  calc: indicatorCalcs.vwap,
});
