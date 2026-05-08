// Reference:
// - Chester W. Keltner, 1960; Linda Bradford Raschke ATR modification
//   https://www.investopedia.com/articles/forex/06/bandschannels.asp
// - TradingView Pine Script EMA/ATR cross-reference
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: Uses modern ATR variant: EMA(typical price) +/- multiplier * ATR.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const kcIndicator = createCustomIndicator({
  name: 'KC',
  shortName: 'KC',
  series: 'price',
  precision: 2,
  calcParams: [20, 2],
  shouldOhlc: true,
  figures: [
    { key: 'upper', title: 'Upper: ', type: 'line' },
    { key: 'middle', title: 'Middle: ', type: 'line' },
    { key: 'lower', title: 'Lower: ', type: 'line' },
  ],
  calc: indicatorCalcs.kc,
});
