// Reference:
// - Money Flow Index overview and formula
//   https://www.investopedia.com/terms/m/mfi.asp
// - TradingView Pine Script ta.mfi built-in
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: Default period is 14; formula applies RSI-style positive/negative money-flow ratios.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const mfiIndicator = createCustomIndicator({
  name: 'MFI',
  shortName: 'MFI',
  series: 'normal',
  precision: 2,
  calcParams: [14],
  shouldOhlc: true,
  figures: [
    { key: 'mfi', title: 'MFI: ', type: 'line' },
  ],
  calc: indicatorCalcs.mfi,
});
