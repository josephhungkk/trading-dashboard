// Reference:
// - Chande Momentum Oscillator overview and formula
//   https://www.investopedia.com/terms/c/chandemomentumoscillator.asp
// - TradingView Pine Script ta.cmo built-in
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: Default period is 14; output is 100 * (sum gains - sum losses) / total movement.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const cmoIndicator = createCustomIndicator({
  name: 'CMO',
  shortName: 'CMO',
  series: 'normal',
  precision: 2,
  calcParams: [14],
  shouldOhlc: true,
  figures: [
    { key: 'cmo', title: 'CMO: ', type: 'line' },
  ],
  calc: indicatorCalcs.cmo,
});
