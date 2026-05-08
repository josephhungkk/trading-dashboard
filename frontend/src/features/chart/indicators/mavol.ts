// Reference:
// - Moving average overview
//   https://en.wikipedia.org/wiki/Moving_average
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Moving Average Volume is the SMA-volume alias; kept distinct from AVGVOL by output key/name.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const mavolIndicator = createCustomIndicator({
  name: 'MAVOL',
  shortName: 'MAVOL',
  series: 'volume',
  precision: 2,
  calcParams: [20],
  shouldOhlc: true,
  figures: [
    { key: 'mavol', title: 'MAVOL: ', type: 'line' },
  ],
  calc: indicatorCalcs.mavol,
});
