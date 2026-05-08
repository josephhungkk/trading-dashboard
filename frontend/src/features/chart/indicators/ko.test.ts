import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { koIndicator } from './ko';
import golden from './__golden__/ko.json';

describe('ko indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(koIndicator, golden);
  });
});
