import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { eneIndicator } from './ene';
import golden from './__golden__/ene.json';

describe('ene indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(eneIndicator, golden);
  });
});
