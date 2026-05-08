// Reference:
// - Klinger Oscillator overview and formula
//   https://www.investopedia.com/terms/k/klingeroscillator.asp
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Defaults are 34/55 EMAs of volume force with a 13-period signal line.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const koIndicator = createCustomIndicator({
  name: 'KO',
  shortName: 'KO',
  series: 'normal',
  precision: 2,
  calcParams: [34, 55, 13],
  shouldOhlc: true,
  figures: [
    { key: 'ko', title: 'KO: ', type: 'line' },
    { key: 'signal', title: 'SIGNAL: ', type: 'line' },
  ],
  calc: indicatorCalcs.ko,
});
