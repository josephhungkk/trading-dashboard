import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { gmmaIndicator } from './gmma';
import golden from './__golden__/gmma.json';

describe('gmma indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(gmmaIndicator, golden);
  });
});
