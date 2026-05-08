// Reference:
// - MIKE support/resistance indicator reference
//   https://www.fmlabs.com/reference/default.htm?url=MIKE.htm
// - Typical price cross-reference
//   https://www.investopedia.com/terms/t/typicalprice.asp
//
// Notes: Base variant requested by Phase 9: previous typical price +/- previous range.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const mikeBaseIndicator = createCustomIndicator({
  name: 'MIKE_BASE',
  shortName: 'MIKE_BASE',
  series: 'price',
  precision: 2,
  calcParams: [],
  shouldOhlc: true,
  figures: [
    { key: 's1', title: 'S1: ', type: 'line' },
    { key: 'm', title: 'M: ', type: 'line' },
    { key: 'r1', title: 'R1: ', type: 'line' },
  ],
  calc: indicatorCalcs.mike_base,
});
