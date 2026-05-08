import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { cksIndicator } from './cks';
import golden from './__golden__/cks.json';

describe('cks indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(cksIndicator, golden);
  });
});
