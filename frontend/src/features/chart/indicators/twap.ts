// Reference:
// - Time-weighted average price
//   https://en.wikipedia.org/wiki/Time-weighted_average_price
// - Typical price cross-reference
//   https://www.investopedia.com/terms/t/typicalprice.asp
//
// Notes: Cumulative average of typical price over the loaded data window.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const twapIndicator = createCustomIndicator({
  name: 'TWAP',
  shortName: 'TWAP',
  series: 'price',
  precision: 2,
  calcParams: [],
  shouldOhlc: true,
  figures: [
    { key: 'twap', title: 'TWAP: ', type: 'line' },
  ],
  calc: indicatorCalcs.twap,
});
