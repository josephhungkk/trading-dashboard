import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { kcIndicator } from './kc';
import golden from './__golden__/kc.json';

describe('kc indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(kcIndicator, golden);
  });
});
