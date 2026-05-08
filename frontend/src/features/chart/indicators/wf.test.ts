import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { wfIndicator } from './wf';
import golden from './__golden__/wf.json';

describe('wf indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(wfIndicator, golden);
  });
});
