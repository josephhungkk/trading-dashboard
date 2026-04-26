export interface ParsedDecimal {
  display: number;
  precise: string;
  lossy: boolean;
}

export function safeParseDecimal(s: string): ParsedDecimal {
  if (!s) return { display: 0, precise: '0', lossy: false };
  const n = Number(s);
  return {
    display: Number.isFinite(n) ? n : 0,
    precise: s,
    lossy: !Number.isFinite(n) || n.toString() !== s,
  };
}
