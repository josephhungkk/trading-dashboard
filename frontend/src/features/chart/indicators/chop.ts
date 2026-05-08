// Reference:
// - Choppiness Index formula
//   https://www.tradingview.com/support/solutions/43000501980-choppiness-index-chop/
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Default period is 14; uses log10(sum(TR, n) / high-low range) / log10(n).

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const chopIndicator = createCustomIndicator({
  name: 'CHOP',
  shortName: 'CHOP',
  series: 'normal',
  precision: 2,
  calcParams: [14],
  shouldOhlc: true,
  figures: [
    { key: 'chop', title: 'CHOP: ', type: 'line' },
  ],
  calc: indicatorCalcs.chop,
});
