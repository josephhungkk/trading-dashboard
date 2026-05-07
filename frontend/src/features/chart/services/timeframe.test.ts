import { describe, it, expect } from 'vitest';
import { parseTimeframeMs, defaultWindowMs } from './timeframe';

describe('parseTimeframeMs', () => {
  it.each([
    ['1s', 1_000],
    ['5s', 5_000],
    ['1m', 60_000],
    ['5m', 300_000],
    ['15m', 900_000],
    ['1h', 3_600_000],
    ['4h', 14_400_000],
    ['1d', 86_400_000],
    ['1w', 7 * 86_400_000],
    ['1M', 30 * 86_400_000],
  ])('parses %s → %i ms', (tf, expected) => {
    expect(parseTimeframeMs(tf)).toBe(expected);
  });

  it('throws on unknown format', () => {
    expect(() => parseTimeframeMs('1x')).toThrow('unknown timeframe: 1x');
  });

  it('throws on empty string', () => {
    expect(() => parseTimeframeMs('')).toThrow('unknown timeframe');
  });

  it('throws on bare unit with no number', () => {
    expect(() => parseTimeframeMs('m')).toThrow('unknown timeframe');
  });
});

describe('defaultWindowMs', () => {
  it('returns 10000 × interval for short timeframes', () => {
    // 1m × 10000 = 600_000_000 ms (< 10 year cap)
    expect(defaultWindowMs('1m')).toBe(60_000 * 10_000);
  });

  it('returns 10000 × interval for 1s (below cap)', () => {
    expect(defaultWindowMs('1s')).toBe(1_000 * 10_000); // 10_000_000 ms
  });

  it('caps at 10 years for 1w (1w × 10000 >> 10 years)', () => {
    const tenYearsMs = 10 * 365 * 24 * 60 * 60 * 1000;
    expect(defaultWindowMs('1w')).toBe(tenYearsMs);
  });

  it('caps at 10 years for 1M', () => {
    const tenYearsMs = 10 * 365 * 24 * 60 * 60 * 1000;
    expect(defaultWindowMs('1M')).toBe(tenYearsMs);
  });

  it('caps at 10 years for 1d (1d × 10000 = 27.4 years)', () => {
    const tenYearsMs = 10 * 365 * 24 * 60 * 60 * 1000;
    expect(defaultWindowMs('1d')).toBe(tenYearsMs);
  });
});
