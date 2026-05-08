// Reference:
// - Heikin-Ashi calculation overview
//   https://www.investopedia.com/trading/heikin-ashi-better-candlestick/
// - klinecharts indicator source cross-reference
//   https://github.com/klinecharts/KLineChart
//
// Notes: Outputs Heikin-Ashi close-open diff and a signed flip marker when bar color changes.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const hadiffIndicator = createCustomIndicator({
  name: 'HADIFF',
  shortName: 'HADIFF',
  series: 'normal',
  precision: 4,
  calcParams: [],
  shouldOhlc: true,
  figures: [
    { key: 'diff', title: 'DIFF: ', type: 'line' },
    { key: 'flip', title: 'FLIP: ', type: 'line' },
  ],
  calc: indicatorCalcs.hadiff,
});
