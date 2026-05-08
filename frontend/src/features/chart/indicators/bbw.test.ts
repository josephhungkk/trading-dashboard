import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { bbwIndicator } from './bbw';
import golden from './__golden__/bbw.json';

describe('bbw indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(bbwIndicator, golden);
  });
});
