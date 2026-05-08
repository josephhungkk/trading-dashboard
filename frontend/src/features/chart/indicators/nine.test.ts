import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { nineIndicator } from './nine';
import golden from './__golden__/nine.json';

describe('nine indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(nineIndicator, golden);
  });
});
