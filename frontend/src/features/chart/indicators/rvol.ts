// Reference:
// - Relative Volume indicator overview
//   https://www.tradingview.com/support/solutions/43000635874-relative-volume-at-time/
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Default period is 20; output is current volume divided by average volume.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const rvolIndicator = createCustomIndicator({
  name: 'RVOL',
  shortName: 'RVOL',
  series: 'volume',
  precision: 4,
  calcParams: [20],
  shouldOhlc: true,
  figures: [
    { key: 'rvol', title: 'RVOL: ', type: 'line' },
  ],
  calc: indicatorCalcs.rvol,
});
