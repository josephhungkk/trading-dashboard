import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { rvolIndicator } from './rvol';
import golden from './__golden__/rvol.json';

describe('rvol indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(rvolIndicator, golden);
  });
});
