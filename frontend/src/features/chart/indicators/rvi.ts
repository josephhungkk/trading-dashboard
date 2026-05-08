// Reference:
// - Relative Vigor Index overview and formula
//   https://www.investopedia.com/terms/r/relative_vigor_index.asp
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Default period is 10; uses 4-bar weighted close-open and high-low smoothing.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const rviIndicator = createCustomIndicator({
  name: 'RVI',
  shortName: 'RVI',
  series: 'normal',
  precision: 4,
  calcParams: [10],
  shouldOhlc: true,
  figures: [
    { key: 'rvi', title: 'RVI: ', type: 'line' },
    { key: 'signal', title: 'SIGNAL: ', type: 'line' },
  ],
  calc: indicatorCalcs.rvi,
});
