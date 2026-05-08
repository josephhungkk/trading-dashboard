import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { bopIndicator } from './bop';
import golden from './__golden__/bop.json';

describe('bop indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(bopIndicator, golden);
  });
});
