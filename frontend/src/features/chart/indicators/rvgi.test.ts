import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { rvgiIndicator } from './rvgi';
import golden from './__golden__/rvgi.json';

describe('rvgi indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(rvgiIndicator, golden);
  });
});
