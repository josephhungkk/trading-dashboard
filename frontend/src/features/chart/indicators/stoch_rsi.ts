// Reference:
// - Stochastic RSI overview and formula
//   https://www.investopedia.com/terms/s/stochrsi.asp
// - TradingView Pine Script ta.stoch built-in
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: Defaults are RSI 14, stochastic window 14, K smoothing 3, D smoothing 3.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const stochRsiIndicator = createCustomIndicator({
  name: 'STOCH_RSI',
  shortName: 'STOCH RSI',
  series: 'normal',
  precision: 2,
  calcParams: [14, 14, 3, 3],
  shouldOhlc: true,
  figures: [
    { key: 'k', title: 'K: ', type: 'line' },
    { key: 'd', title: 'D: ', type: 'line' },
  ],
  calc: indicatorCalcs.stoch_rsi,
});
