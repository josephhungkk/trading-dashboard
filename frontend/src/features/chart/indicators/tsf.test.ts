import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { tsfIndicator } from './tsf';
import golden from './__golden__/tsf.json';

describe('tsf indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(tsfIndicator, golden);
  });
});
