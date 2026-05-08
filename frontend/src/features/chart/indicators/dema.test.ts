import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { demaIndicator } from './dema';
import golden from './__golden__/dema.json';

describe('dema indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(demaIndicator, golden);
  });
});
