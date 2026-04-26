import { describe, expect, it } from 'vitest';
import { safeParseDecimal } from './decimal';

describe('safeParseDecimal', () => {
  it.each([
    ['', { display: 0, precise: '0', lossy: false }],
    ['0', { display: 0, precise: '0', lossy: false }],
    ['1.5', { display: 1.5, precise: '1.5', lossy: false }],
    ['123.45', { display: 123.45, precise: '123.45', lossy: false }],
    ['100.00', { display: 100, precise: '100.00', lossy: true }],
    ['0.10000000', { display: 0.1, precise: '0.10000000', lossy: true }],
    ['-1.5', { display: -1.5, precise: '-1.5', lossy: false }],
    ['NaN', { display: 0, precise: 'NaN', lossy: true }],
    ['Infinity', { display: 0, precise: 'Infinity', lossy: true }],
    ['abc', { display: 0, precise: 'abc', lossy: true }],
    [
      '99999999999999999999',
      {
        display: 100000000000000000000,
        precise: '99999999999999999999',
        lossy: true,
      },
    ],
  ])('parses %s', (input, expected) => {
    expect(safeParseDecimal(input)).toEqual(expected);
  });
});
