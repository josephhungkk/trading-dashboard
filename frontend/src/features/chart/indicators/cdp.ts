// Reference:
// - Contrarian Day Pivot formula reference
//   https://www.investopedia.com/terms/p/pivotpoint.asp
// - klinecharts indicator template cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Uses previous bar H/L/C arithmetic: CDP=(H+L+2C)/4 with AH/NH/NL/AL levels.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const cdpIndicator = createCustomIndicator({
  name: 'CDP',
  shortName: 'CDP',
  series: 'price',
  precision: 2,
  calcParams: [],
  shouldOhlc: true,
  figures: [
    { key: 'cdp', title: 'CDP: ', type: 'line' },
    { key: 'ah', title: 'AH: ', type: 'line' },
    { key: 'nh', title: 'NH: ', type: 'line' },
    { key: 'nl', title: 'NL: ', type: 'line' },
    { key: 'al', title: 'AL: ', type: 'line' },
  ],
  calc: indicatorCalcs.cdp,
});
