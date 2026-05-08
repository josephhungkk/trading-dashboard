import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { rmiIndicator } from './rmi';
import golden from './__golden__/rmi.json';

describe('rmi indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(rmiIndicator, golden);
  });
});
