// Reference:
// - Price oscillator and momentum oscillator definitions
//   https://www.investopedia.com/terms/p/price_oscillator.asp
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Default is a 10-period percentage momentum oscillator.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const oscIndicator = createCustomIndicator({
  name: 'OSC',
  shortName: 'OSC',
  series: 'normal',
  precision: 2,
  calcParams: [10],
  shouldOhlc: true,
  figures: [
    { key: 'osc', title: 'OSC: ', type: 'line' },
  ],
  calc: indicatorCalcs.osc,
});
