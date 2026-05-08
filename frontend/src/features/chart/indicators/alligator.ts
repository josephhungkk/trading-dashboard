// Reference:
// - Bill Williams, 1995, Trading Chaos Alligator indicator
//   https://www.investopedia.com/articles/trading/072115/exploring-williams-alligator-indicator.asp
// - TradingView Pine Script ta.rma/SMMA cross-reference
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: Bill Williams, 1995. Uses SMMA of median price; jaw/teeth/lips are shifted 8/5/3 bars.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const alligatorIndicator = createCustomIndicator({
  name: 'ALLIGATOR',
  shortName: 'ALLIGATOR',
  series: 'price',
  precision: 2,
  calcParams: [13, 8, 8, 5, 5, 3],
  shouldOhlc: true,
  figures: [
    { key: 'jaw', title: 'Jaw: ', type: 'line' },
    { key: 'teeth', title: 'Teeth: ', type: 'line' },
    { key: 'lips', title: 'Lips: ', type: 'line' },
  ],
  calc: indicatorCalcs.alligator,
});
