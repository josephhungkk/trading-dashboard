// Reference:
// - Kaufman Adaptive Moving Average efficiency ratio definition
//   https://www.investopedia.com/terms/k/kaufmans-adaptive-moving-average-kama.asp
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Default period is 10; formula is net absolute change divided by summed absolute changes.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const erIndicator = createCustomIndicator({
  name: 'ER',
  shortName: 'ER',
  series: 'normal',
  precision: 4,
  calcParams: [10],
  shouldOhlc: true,
  figures: [
    { key: 'er', title: 'ER: ', type: 'line' },
  ],
  calc: indicatorCalcs.er,
});
