import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { cmoIndicator } from './cmo';
import golden from './__golden__/cmo.json';

describe('cmo indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(cmoIndicator, golden);
  });
});
