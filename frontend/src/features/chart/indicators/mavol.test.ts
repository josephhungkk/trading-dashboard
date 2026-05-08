import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { mavolIndicator } from './mavol';
import golden from './__golden__/mavol.json';

describe('mavol indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(mavolIndicator, golden);
  });
});
