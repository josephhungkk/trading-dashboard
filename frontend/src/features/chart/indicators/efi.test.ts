import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { efiIndicator } from './efi';
import golden from './__golden__/efi.json';

describe('efi indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(efiIndicator, golden);
  });
});
