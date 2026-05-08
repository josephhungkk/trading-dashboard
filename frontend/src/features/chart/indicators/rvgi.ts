// Reference:
// - Relative Vigor Index overview and formula
//   https://www.investopedia.com/terms/r/relative_vigor_index.asp
// - TradingView Pine Script built-ins reference
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: Generalized variant keeps the RVI numerator/denominator but uses EMA smoothing.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const rvgiIndicator = createCustomIndicator({
  name: 'RVGI',
  shortName: 'RVGI',
  series: 'normal',
  precision: 4,
  calcParams: [10],
  shouldOhlc: true,
  figures: [
    { key: 'rvgi', title: 'RVGI: ', type: 'line' },
    { key: 'signal', title: 'SIGNAL: ', type: 'line' },
  ],
  calc: indicatorCalcs.rvgi,
});
