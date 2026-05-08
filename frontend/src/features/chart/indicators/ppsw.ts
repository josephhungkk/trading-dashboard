// Reference:
// - J. Welles Wilder Jr., 1978, Average True Range
//   https://en.wikipedia.org/wiki/Average_true_range
// - Pivot point cross-reference
//   https://www.investopedia.com/terms/p/pivotpoint.asp
//
// Notes: Phase 9 ATR-band variant: typical price center with +/- multiplier * ATR and explicit width.

import { createCustomIndicator, indicatorCalcs } from './_shared';

export const ppswIndicator = createCustomIndicator({
  name: 'PPSW',
  shortName: 'PPSW',
  series: 'price',
  precision: 2,
  calcParams: [14, 1],
  shouldOhlc: true,
  figures: [
    { key: 'upper', title: 'Upper: ', type: 'line' },
    { key: 'middle', title: 'Middle: ', type: 'line' },
    { key: 'lower', title: 'Lower: ', type: 'line' },
    { key: 'width', title: 'Width: ', type: 'line' },
  ],
  calc: indicatorCalcs.ppsw,
});
