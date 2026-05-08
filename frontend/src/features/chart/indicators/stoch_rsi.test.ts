import { describe, it } from 'vitest';
import { expectGoldenVector } from './_testUtils';
import { stochRsiIndicator } from './stoch_rsi';
import golden from './__golden__/stoch_rsi.json';

describe('stoch_rsi indicator - golden vector', () => {
  it('matches expected output for synthetic 200-bar input', () => {
    expectGoldenVector(stochRsiIndicator, golden);
  });
});
