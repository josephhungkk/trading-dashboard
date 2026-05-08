// Reference:
// - Moving average overview
//   https://en.wikipedia.org/wiki/Moving_average
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Average Volume is a default 20-period simple moving average of volume.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const avgvolIndicator = createCustomIndicator({
  name: 'AVGVOL',
  shortName: 'AVGVOL',
  series: 'volume',
  precision: 2,
  calcParams: [20],
  shouldOhlc: true,
  figures: [
    { key: 'avgvol', title: 'AVGVOL: ', type: 'line' },
  ],
  calc: indicatorCalcs.avgvol,
});
