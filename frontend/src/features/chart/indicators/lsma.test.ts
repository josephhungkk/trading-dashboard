import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { lsmaIndicator } from './lsma';
import golden from './__golden__/lsma.json';

describe('lsma indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(lsmaIndicator, golden);
  });
});
