import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { chopIndicator } from './chop';
import golden from './__golden__/chop.json';

describe('chop indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(chopIndicator, golden);
  });
});
