import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { ichimokuIndicator } from './ichimoku';
import golden from './__golden__/ichimoku.json';

describe('ichimoku indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(ichimokuIndicator, golden);
  });
});
