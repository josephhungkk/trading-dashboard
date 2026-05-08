// Reference:
// - Elder Force Index overview and formula
//   https://www.investopedia.com/terms/f/force-index.asp
// - TradingView Pine Script ta.ema built-in
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: Default period is 13; raw force is volume multiplied by close-to-close change, EMA smoothed.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const efiIndicator = createCustomIndicator({
  name: 'EFI',
  shortName: 'EFI',
  series: 'normal',
  precision: 2,
  calcParams: [13],
  shouldOhlc: true,
  figures: [
    { key: 'efi', title: 'EFI: ', type: 'line' },
  ],
  calc: indicatorCalcs.efi,
});
