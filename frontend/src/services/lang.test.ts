import { describe, it, expect } from 'vitest';
import { langForMarket } from './lang';

describe('langForMarket', () => {
  it.each([
    ['NYSE', 'en'], ['SEHK', 'zh-HK'], ['TSE', 'ja'], ['KRX', 'ko'],
    ['TWSE', 'zh-TW'], ['SSE', 'zh-CN'], ['FX', 'en'], ['Unknown', 'en'],
  ])('%s → %s', (input, expected) => {
    expect(langForMarket(input)).toBe(expected);
  });
});
