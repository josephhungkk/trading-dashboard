import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { erIndicator } from './er';
import golden from './__golden__/er.json';

describe('er indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(erIndicator, golden);
  });
});
