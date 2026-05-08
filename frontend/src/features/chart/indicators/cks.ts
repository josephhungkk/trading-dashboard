// Reference:
// - Tushar Chande and Stanley Kroll, 1994, The New Technical Trader
//   https://www.tradingview.com/support/solutions/43000589105-chande-kroll-stop/
// - Average True Range cross-reference
//   https://en.wikipedia.org/wiki/Average_true_range
//
// Notes: ATR-based Chande Kroll Stop with long/short stop bands smoothed over the stop period.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const cksIndicator = createCustomIndicator({
  name: 'CKS',
  shortName: 'CKS',
  series: 'price',
  precision: 2,
  calcParams: [10, 9, 1],
  shouldOhlc: true,
  figures: [
    { key: 'longStop', title: 'Long: ', type: 'line' },
    { key: 'shortStop', title: 'Short: ', type: 'line' },
  ],
  calc: indicatorCalcs.cks,
});
