import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { hadiffIndicator } from './hadiff';
import golden from './__golden__/hadiff.json';

describe('hadiff indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(hadiffIndicator, golden);
  });
});
