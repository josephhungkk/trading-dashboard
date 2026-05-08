import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { fisherIndicator } from './fisher';
import golden from './__golden__/fisher.json';

describe('fisher indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(fisherIndicator, golden);
  });
});
