import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { dcIndicator } from './dc';
import golden from './__golden__/dc.json';

describe('dc indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(dcIndicator, golden);
  });
});
