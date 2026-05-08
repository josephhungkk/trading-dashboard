import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { crsiIndicator } from './crsi';
import golden from './__golden__/crsi.json';

describe('crsi indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(crsiIndicator, golden);
  });
});
