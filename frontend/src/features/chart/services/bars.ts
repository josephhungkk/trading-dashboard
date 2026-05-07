export interface BarItem {
  bucket_start: string; // ISO8601
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  trade_count: number;
}

export interface BarPage {
  bars: BarItem[];
  next_cursor: string | null;
}

export interface FetchBarsParams {
  canonicalId: string;
  timeframe: string;
  start: Date;
  end: Date;
  cursor?: string;
  limit?: number;
  signal?: AbortSignal;
}

export async function fetchBars(params: FetchBarsParams): Promise<BarPage> {
  const url = new URL('/api/bars', window.location.origin);
  url.searchParams.set('canonical_id', params.canonicalId);
  url.searchParams.set('timeframe', params.timeframe);
  url.searchParams.set('start', params.start.toISOString());
  url.searchParams.set('end', params.end.toISOString());
  if (params.cursor) url.searchParams.set('cursor', params.cursor);
  // MED-5: guard against NaN / non-positive limit values
  if (Number.isFinite(params.limit) && (params.limit ?? 0) > 0) {
    url.searchParams.set('limit', String(params.limit));
  }

  // HIGH-4: pass AbortSignal when provided; omit the property entirely when absent
  // to satisfy exactOptionalPropertyTypes (RequestInit.signal is AbortSignal | null, not | undefined).
  const fetchInit: RequestInit = { credentials: 'same-origin' };
  if (params.signal != null) fetchInit.signal = params.signal;
  const res = await fetch(url.toString(), fetchInit);
  if (!res.ok) throw new Error(`bars fetch failed: ${res.status}`);
  return res.json() as Promise<BarPage>;
}

// klinecharts data adapter — converts NUMERIC strings to numbers ONLY for
// the chart's data array; preserve strings everywhere else.
export interface ChartBar {
  timestamp: number; // ms
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  [key: string]: unknown; // satisfies klinecharts KLineData index signature
}

export function toChartBars(bars: BarItem[]): ChartBar[] {
  return bars.map((b) => ({
    timestamp: new Date(b.bucket_start).getTime(),
    open: Number(b.open),
    high: Number(b.high),
    low: Number(b.low),
    close: Number(b.close),
    volume: Number(b.volume),
  }));
}
