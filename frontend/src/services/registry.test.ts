import { describe, it, expect, beforeEach } from 'vitest';
import { getServices, resetServices } from './registry';

describe('services registry', () => {
  beforeEach(resetServices);
  it('memoizes — same instance on repeat', () => {
    expect(getServices()).toBe(getServices());
  });
  it('resetServices yields fresh instance', () => {
    const a = getServices();
    resetServices();
    expect(getServices()).not.toBe(a);
  });
});
