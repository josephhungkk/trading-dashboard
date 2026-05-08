// Reference:
// - Rate of change definition
//   https://en.wikipedia.org/wiki/Rate_of_change
// - TradingView Pine Script ta.roc built-in
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: ROC alias; default period is 12 and output is percentage change.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const rcIndicator = createCustomIndicator({
  name: 'RC',
  shortName: 'RC',
  series: 'normal',
  precision: 2,
  calcParams: [12],
  shouldOhlc: true,
  figures: [
    { key: 'rc', title: 'RC: ', type: 'line' },
  ],
  calc: indicatorCalcs.rc,
});
