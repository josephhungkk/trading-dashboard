// Reference:
// - Daryl Guppy, 1997, Guppy Multiple Moving Average
//   https://www.guppytraders.com/gup329.shtml
// - TradingView Pine Script EMA built-in cross-reference
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
//
// Notes: Daryl Guppy, 1997. Uses six short EMAs and six long EMAs with standard GMMA periods.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const gmmaIndicator = createCustomIndicator({
  name: 'GMMA',
  shortName: 'GMMA',
  series: 'price',
  precision: 2,
  calcParams: [3, 5, 8, 10, 12, 15, 30, 35, 40, 45, 50, 60],
  shouldOhlc: true,
  figures: [
    { key: 'ema3', title: 'EMA3: ', type: 'line' },
    { key: 'ema5', title: 'EMA5: ', type: 'line' },
    { key: 'ema8', title: 'EMA8: ', type: 'line' },
    { key: 'ema10', title: 'EMA10: ', type: 'line' },
    { key: 'ema12', title: 'EMA12: ', type: 'line' },
    { key: 'ema15', title: 'EMA15: ', type: 'line' },
    { key: 'ema30', title: 'EMA30: ', type: 'line' },
    { key: 'ema35', title: 'EMA35: ', type: 'line' },
    { key: 'ema40', title: 'EMA40: ', type: 'line' },
    { key: 'ema45', title: 'EMA45: ', type: 'line' },
    { key: 'ema50', title: 'EMA50: ', type: 'line' },
    { key: 'ema60', title: 'EMA60: ', type: 'line' },
  ],
  calc: indicatorCalcs.gmma,
});
