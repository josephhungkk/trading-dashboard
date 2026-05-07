/**
 * Shared timeframe utilities used by TradeChart, bars service, and liveTail service.
 */

/**
 * Parses a timeframe string into milliseconds.
 * Supports: s (seconds), m (minutes), h (hours), d (days), w (weeks), M (months).
 * Throws on unrecognised format.
 */
export function parseTimeframeMs(tf: string): number {
  const m = /^(\d+)([smhdwM])$/.exec(tf);
  if (!m) throw new Error(`unknown timeframe: ${tf}`);
  const n = Number(m[1]);
  switch (m[2]) {
    case 's': return n * 1000;
    case 'm': return n * 60 * 1000;
    case 'h': return n * 60 * 60 * 1000;
    case 'd': return n * 24 * 60 * 60 * 1000;
    case 'w': return n * 7 * 24 * 60 * 60 * 1000;
    case 'M': return n * 30 * 24 * 60 * 60 * 1000;
    default: throw new Error(`unknown timeframe: ${tf}`);
  }
}

/**
 * Derives the fetch window duration in milliseconds from a timeframe string.
 * Targets ~10 000 bars while capping at 10 years.
 */
export function defaultWindowMs(tf: string): number {
  const interval = parseTimeframeMs(tf);
  return Math.min(interval * 10_000, 10 * 365 * 24 * 60 * 60 * 1000);
}
