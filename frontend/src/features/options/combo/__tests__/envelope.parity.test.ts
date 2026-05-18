import { describe, expect, it } from 'vitest';
import { computeEnvelope } from '../computeEnvelope';
import type { LegInput } from '../computeEnvelope';
import goldenFixtures from '../../../../../../backend/tests/services/combos/fixtures/golden_envelopes.json';

interface GoldenFixture {
  strategy: string;
  legs: (LegInput & { mid: string })[];
  expected: {
    kind: 'DEBIT' | 'CREDIT';
    net_debit_credit: string;
    max_loss: string | null;
    max_profit: string | null;
    break_even: string[];
  };
}

describe('computeEnvelope parity with Python backend', () => {
  it.each(goldenFixtures as GoldenFixture[])('$strategy golden fixture matches', (fixture) => {
    const mids: Record<number, string> = Object.fromEntries(
      fixture.legs.map((l, i) => [i, l.mid]),
    );
    const result = computeEnvelope(fixture.strategy, fixture.legs, mids);
    expect(result.net_debit_credit).toBe(fixture.expected.net_debit_credit);
    expect(result.kind).toBe(fixture.expected.kind);
    expect(result.max_loss).toBe(fixture.expected.max_loss);
    expect(result.max_profit).toBe(fixture.expected.max_profit);
    expect(result.break_even).toEqual(fixture.expected.break_even);
  });
});
