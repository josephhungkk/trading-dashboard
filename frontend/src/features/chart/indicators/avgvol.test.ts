import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { avgvolIndicator } from './avgvol';
import golden from './__golden__/avgvol.json';

describe('avgvol indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(avgvolIndicator, golden);
  });
});
