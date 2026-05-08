// Reference:
// - Fisher Transform indicator overview
//   https://www.tradingview.com/support/solutions/43000589141-fisher-transform/
// - TradingView Pine Script built-ins reference
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: Default period is 10; median price is normalized and transformed with a trigger line.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const fisherIndicator = createCustomIndicator({
  name: 'FISHER',
  shortName: 'FISHER',
  series: 'normal',
  precision: 4,
  calcParams: [10],
  shouldOhlc: true,
  figures: [
    { key: 'fisher', title: 'FISHER: ', type: 'line' },
    { key: 'trigger', title: 'TRIGGER: ', type: 'line' },
  ],
  calc: indicatorCalcs.fisher,
});
