// Reference:
// - Forecast Oscillator overview and formula
//   https://www.tradingview.com/support/solutions/43000589189-forecast-oscillator/
// - TradingView Pine Script ta.linreg built-in
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: Default period is 14; output is close deviation from LSMA forecast as a percent.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const foIndicator = createCustomIndicator({
  name: 'FO',
  shortName: 'FO',
  series: 'normal',
  precision: 2,
  calcParams: [14],
  shouldOhlc: true,
  figures: [
    { key: 'fo', title: 'FO: ', type: 'line' },
  ],
  calc: indicatorCalcs.fo,
});
