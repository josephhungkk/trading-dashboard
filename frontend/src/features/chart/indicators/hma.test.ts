import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { hmaIndicator } from './hma';
import golden from './__golden__/hma.json';

describe('hma indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(hmaIndicator, golden);
  });
});
