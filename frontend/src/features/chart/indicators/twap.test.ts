import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { twapIndicator } from './twap';
import golden from './__golden__/twap.json';

describe('twap indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(twapIndicator, golden);
  });
});
