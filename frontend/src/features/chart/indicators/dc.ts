// Reference:
// - Richard Donchian, Donchian Channel
//   https://en.wikipedia.org/wiki/Donchian_channel
// - TradingView Pine Script highest/lowest cross-reference
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: Upper/lower are highest high and lowest low over n; middle is their midpoint.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const dcIndicator = createCustomIndicator({
  name: 'DC',
  shortName: 'DC',
  series: 'price',
  precision: 2,
  calcParams: [20],
  shouldOhlc: true,
  figures: [
    { key: 'upper', title: 'Upper: ', type: 'line' },
    { key: 'middle', title: 'Middle: ', type: 'line' },
    { key: 'lower', title: 'Lower: ', type: 'line' },
  ],
  calc: indicatorCalcs.dc,
});
