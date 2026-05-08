// Reference:
// - Balance of Power overview and formula
//   https://www.tradingview.com/support/solutions/43000589183-balance-of-power-bop/
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Formula is (close - open) / (high - low); zero-range bars return null.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const bopIndicator = createCustomIndicator({
  name: 'BOP',
  shortName: 'BOP',
  series: 'normal',
  precision: 4,
  calcParams: [],
  shouldOhlc: true,
  figures: [
    { key: 'bop', title: 'BOP: ', type: 'line' },
  ],
  calc: indicatorCalcs.bop,
});
