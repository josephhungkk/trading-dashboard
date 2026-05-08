// Reference:
// - Moving average envelope
//   https://www.investopedia.com/terms/m/movingaverageenvelope.asp
// - klinecharts moving average implementation cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Middle is SMA(close, n); bands are middle +/- percentage.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const eneIndicator = createCustomIndicator({
  name: 'ENE',
  shortName: 'ENE',
  series: 'price',
  precision: 2,
  calcParams: [20, 6],
  shouldOhlc: true,
  figures: [
    { key: 'upper', title: 'Upper: ', type: 'line' },
    { key: 'middle', title: 'Middle: ', type: 'line' },
    { key: 'lower', title: 'Lower: ', type: 'line' },
  ],
  calc: indicatorCalcs.ene,
});
