import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { rviIndicator } from './rvi';
import golden from './__golden__/rvi.json';

describe('rvi indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(rviIndicator, golden);
  });
});
