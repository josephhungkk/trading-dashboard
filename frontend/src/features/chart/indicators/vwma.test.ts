import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { vwmaIndicator } from './vwma';
import golden from './__golden__/vwma.json';

describe('vwma indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(vwmaIndicator, golden);
  });
});
