import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { cdpIndicator } from './cdp';
import golden from './__golden__/cdp.json';

describe('cdp indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(cdpIndicator, golden);
  });
});
