// Reference:
// - Williams Fractal indicator definition
//   https://www.investopedia.com/terms/f/fractal.asp
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Marks 5-bar local high and low fractals at the center bar.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const wfIndicator = createCustomIndicator({
  name: 'WF',
  shortName: 'WF',
  series: 'price',
  precision: 2,
  calcParams: [],
  shouldOhlc: true,
  figures: [
    { key: 'highFractal', title: 'HIGH: ', type: 'line' },
    { key: 'lowFractal', title: 'LOW: ', type: 'line' },
  ],
  calc: indicatorCalcs.wf,
});
