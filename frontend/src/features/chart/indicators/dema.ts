// Reference:
// - Patrick G. Mulloy, 1994, Technical Analysis of Stocks & Commodities
//   https://traders.com/documentation/feedbk_docs/1994/01/Abstracts_new/Mulloy.html
// - TradingView Pine Script EMA built-in cross-reference
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: DEMA = 2 * EMA - EMA(EMA); EMA is SMA-seeded.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const demaIndicator = createCustomIndicator({
  name: 'DEMA',
  shortName: 'DEMA',
  series: 'price',
  precision: 2,
  calcParams: [20],
  shouldOhlc: true,
  figures: [
    { key: 'dema', title: 'DEMA: ', type: 'line' },
  ],
  calc: indicatorCalcs.dema,
});
