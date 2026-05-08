// Reference:
// - Relative Momentum Index formula
//   https://www.tradingview.com/support/solutions/43000501970-relative-momentum-index-rmi/
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Defaults are 5-bar momentum and 14-period RSI smoothing of momentum.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const rmiIndicator = createCustomIndicator({
  name: 'RMI',
  shortName: 'RMI',
  series: 'normal',
  precision: 2,
  calcParams: [5, 14],
  shouldOhlc: true,
  figures: [
    { key: 'rmi', title: 'RMI: ', type: 'line' },
  ],
  calc: indicatorCalcs.rmi,
});
