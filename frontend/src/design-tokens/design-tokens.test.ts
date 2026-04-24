import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { colors } from './colors';

const css = readFileSync(resolve(__dirname, '../styles/tailwind.css'), 'utf8');

function extractVar(name: string): string | undefined {
  const re = new RegExp(`--${name}:\\s*([^;]+);`);
  return css.match(re)?.[1]?.trim();
}

describe('design-tokens/CSS parity', () => {
  it.each([
    ['color-bg',       colors.bg],
    ['color-panel',    colors.panel],
    ['color-fg',       colors.fg],
    ['color-positive', colors.positive],
    ['color-accent-live',  colors.accentLive],
    ['color-accent-paper', colors.accentPaper],
  ])('TS token matches CSS var --%s', (name, tsValue) => {
    expect(extractVar(name)).toBe(tsValue);
  });
});
