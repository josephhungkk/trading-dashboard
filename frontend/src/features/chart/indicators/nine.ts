// Reference:
// - TD Sequential setup count overview
//   https://www.investopedia.com/articles/trading/11/indicators-and-strategies-explained.asp
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Counts closes above/below the close four bars earlier, capped at nine by default.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const nineIndicator = createCustomIndicator({
  name: 'NINE',
  shortName: 'NINE',
  series: 'normal',
  precision: 0,
  calcParams: [4, 9],
  shouldOhlc: true,
  figures: [
    { key: 'buy', title: 'BUY: ', type: 'line' },
    { key: 'sell', title: 'SELL: ', type: 'line' },
  ],
  calc: indicatorCalcs.nine,
});
