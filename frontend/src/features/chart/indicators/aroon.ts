// Reference:
// - Aroon indicator overview and formula
//   https://www.investopedia.com/terms/a/aroon.asp
// - TradingView Pine Script ta.aroon built-in
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: Default period is 14; outputs Aroon Up and Aroon Down on a 0-100 scale.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const aroonIndicator = createCustomIndicator({
  name: 'AROON',
  shortName: 'AROON',
  series: 'normal',
  precision: 2,
  calcParams: [14],
  shouldOhlc: true,
  figures: [
    { key: 'up', title: 'UP: ', type: 'line' },
    { key: 'down', title: 'DOWN: ', type: 'line' },
  ],
  calc: indicatorCalcs.aroon,
});
