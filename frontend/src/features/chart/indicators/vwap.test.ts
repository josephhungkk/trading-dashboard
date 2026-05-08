import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { vwapIndicator } from './vwap';
import golden from './__golden__/vwap.json';

describe('vwap indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(vwapIndicator, golden);
  });
});
