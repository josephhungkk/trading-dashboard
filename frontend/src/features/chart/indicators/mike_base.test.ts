import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { mikeBaseIndicator } from './mike_base';
import golden from './__golden__/mike_base.json';

describe('mike_base indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(mikeBaseIndicator, golden);
  });
});
