export const fontFamily = {
  sans: ['"Noto Sans"', 'system-ui', 'sans-serif'],
  mono: ['"Noto Sans Mono"', 'ui-monospace', 'monospace'],
} as const;

export const fontSize = {
  xs: '0.75rem',
  sm: '0.875rem',
  base: '1rem',
  lg: '1.125rem',
  xl: '1.25rem',
  '2xl': '1.5rem',
  '3xl': '1.875rem',
  '4xl': '2.25rem',
} as const;

export const lineHeight = {
  tight: '1.2',
  normal: '1.5',
  relaxed: '1.75',
} as const;
