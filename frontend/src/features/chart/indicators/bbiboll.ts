// Reference:
// - John Bollinger, 1980s, Bollinger Bands
//   https://en.wikipedia.org/wiki/Bollinger_Bands
// - klinecharts BBI and BOLL implementations cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: John Bollinger, 1980s; BBI composite center line uses MA(3,6,12,24), with Bollinger-style standard deviation bands.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const bbibollIndicator = createCustomIndicator({
  name: 'BBIBOLL',
  shortName: 'BBIBOLL',
  series: 'price',
  precision: 2,
  calcParams: [3, 6, 12, 24, 20, 2],
  shouldOhlc: true,
  figures: [
    { key: 'bbi', title: 'BBI: ', type: 'line' },
    { key: 'upper', title: 'Upper: ', type: 'line' },
    { key: 'lower', title: 'Lower: ', type: 'line' },
  ],
  calc: indicatorCalcs.bbiboll,
});
