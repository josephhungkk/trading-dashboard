import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { atrIndicator } from './atr';
import golden from './__golden__/atr.json';

describe('atr indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(atrIndicator, golden);
  });
});
