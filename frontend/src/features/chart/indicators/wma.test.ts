import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { wmaIndicator } from './wma';
import golden from './__golden__/wma.json';

describe('wma indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(wmaIndicator, golden);
  });
});
