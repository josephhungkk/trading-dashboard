// Reference:
// - John Bollinger, 1980s, Bollinger BandWidth
//   https://en.wikipedia.org/wiki/Bollinger_Bands
// - TradingView Bollinger BandWidth cross-reference
//   https://www.tradingview.com/support/solutions/43000501972-bollinger-bandwidth-bbw/
//
// Notes: John Bollinger, 1980s. Returns (upper - lower) / middle.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const bbwIndicator = createCustomIndicator({
  name: 'BBW',
  shortName: 'BBW',
  series: 'normal',
  precision: 2,
  calcParams: [20, 2],
  shouldOhlc: true,
  figures: [
    { key: 'bbw', title: 'BBW: ', type: 'line' },
  ],
  calc: indicatorCalcs.bbw,
});
