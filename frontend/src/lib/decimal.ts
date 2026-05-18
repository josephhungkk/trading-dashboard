export interface ParsedDecimal {
  display: number;
  precise: string;
  lossy: boolean;
}

export function safeParseDecimal(value: string): ParsedDecimal {
  if (value === '') return { display: 0, precise: '0', lossy: false };
  const display = Number(value);
  if (!Number.isFinite(display) || Number.isNaN(display)) {
    return { display: 0, precise: value, lossy: true };
  }
  const lossy = String(display) !== value;
  return { display, precise: value, lossy };
}

export function countDecimals(value: string): number {
  const [, frac] = value.split('.');
  return frac ? frac.length : 0;
}

export function exceedsPrecision(value: string, decimals: number): boolean {
  return countDecimals(value) > decimals;
}
