import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { temaIndicator } from './tema';
import golden from './__golden__/tema.json';

describe('tema indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(temaIndicator, golden);
  });
});
