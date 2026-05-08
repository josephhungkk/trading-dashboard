import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { alligatorIndicator } from './alligator';
import golden from './__golden__/alligator.json';

describe('alligator indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(alligatorIndicator, golden);
  });
});
