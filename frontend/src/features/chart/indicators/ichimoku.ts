// Reference:
// - Goichi Hosoda, 1969, Ichimoku Kinko Hyo
//   https://en.wikipedia.org/wiki/Ichimoku_Kink%C5%8D_Hy%C5%8D
// - TradingView support cross-reference
//   https://www.tradingview.com/support/solutions/43000589152-ichimoku-cloud/
//
// Notes: Goichi Hosoda, 1969. Senkou spans are written forward by displacement when within result bounds; Chikou is written backward.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const ichimokuIndicator = createCustomIndicator({
  name: 'ICHIMOKU',
  shortName: 'ICHIMOKU',
  series: 'price',
  precision: 2,
  calcParams: [9, 26, 52, 26],
  shouldOhlc: true,
  figures: [
    { key: 'tenkan', title: 'Tenkan: ', type: 'line' },
    { key: 'kijun', title: 'Kijun: ', type: 'line' },
    { key: 'senkouA', title: 'Senkou A: ', type: 'line' },
    { key: 'senkouB', title: 'Senkou B: ', type: 'line' },
    { key: 'chikou', title: 'Chikou: ', type: 'line' },
  ],
  calc: indicatorCalcs.ichimoku,
});
