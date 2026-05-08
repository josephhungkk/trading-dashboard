import type { Indicator, IndicatorTemplate, KLineData } from 'klinecharts';
import { expect } from 'vitest';
import type { IndicatorOutput } from './_shared';

interface GoldenFixture {
  input: KLineData[];
  expected: IndicatorOutput[];
}

export function expectGoldenVector(
  indicator: IndicatorTemplate<IndicatorOutput, number>,
  golden: GoldenFixture,
): void {
  const resultOrPromise = indicator.calc(golden.input, indicator as Indicator<IndicatorOutput, number>);
  if (resultOrPromise instanceof Promise) {
    throw new Error('golden-vector helper expects synchronous indicator calculations');
  }
  const result = resultOrPromise;
  expect(result).toHaveLength(golden.expected.length);
  for (let i = 0; i < golden.expected.length; i += 1) {
    const actual = result[i];
    const expected = golden.expected[i];
    expect(actual).toBeDefined();
    expect(expected).toBeDefined();
    for (const [key, expectedValue] of Object.entries(expected ?? {})) {
      const actualValue = actual?.[key] ?? null;
      if (expectedValue == null) {
        expect(actualValue, `row ${i} key ${key}`).toBeNull();
      } else {
        expect(actualValue, `row ${i} key ${key}`).toBeTypeOf('number');
        expect(actualValue as number, `row ${i} key ${key}`).toBeCloseTo(expectedValue, 6);
      }
    }
  }
}
