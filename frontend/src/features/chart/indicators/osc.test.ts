import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { oscIndicator } from './osc';
import golden from './__golden__/osc.json';

describe('osc indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(oscIndicator, golden);
  });
});
