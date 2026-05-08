import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { bbibollIndicator } from './bbiboll';
import golden from './__golden__/bbiboll.json';

describe('bbiboll indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(bbibollIndicator, golden);
  });
});
