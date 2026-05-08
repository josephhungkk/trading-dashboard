// Reference:
// - Connors RSI component definition
//   https://www.tradingview.com/support/solutions/43000502017-connors-rsi-crsi/
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Defaults are 3-period RSI, 2-period streak RSI, and 100-period percent rank.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const crsiIndicator = createCustomIndicator({
  name: 'CRSI',
  shortName: 'CRSI',
  series: 'normal',
  precision: 2,
  calcParams: [3, 2, 100],
  shouldOhlc: true,
  figures: [
    { key: 'crsi', title: 'CRSI: ', type: 'line' },
  ],
  calc: indicatorCalcs.crsi,
});
