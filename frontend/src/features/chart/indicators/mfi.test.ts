import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { mfiIndicator } from './mfi';
import golden from './__golden__/mfi.json';

describe('mfi indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(mfiIndicator, golden);
  });
});
