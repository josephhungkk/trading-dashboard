import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { colors } from './colors';

const css = readFileSync(resolve(__dirname, '../styles/tailwind.css'), 'utf8');
const toKebab = (s: string) => s.replace(/([A-Z])/g, '-$1').toLowerCase();
const extractVar = (name: string) =>
  css.match(new RegExp(`--${name}:\\s*([^;]+);`))?.[1]?.trim();

describe('design-tokens/CSS parity', () => {
  it.each(Object.entries(colors))(
    '--color-%s matches TS colors.%s',
    (key, tsValue) => {
      expect(extractVar(`color-${toKebab(key)}`)).toBe(tsValue);
    },
  );
});
