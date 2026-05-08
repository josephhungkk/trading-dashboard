import { EtagMismatchError, getChartLayout, putChartLayout, type ChartLayout } from './chartLayouts';

export interface LayoutPayload extends Record<string, unknown> {
  timeframe: string;
  indicators: string[];
  drawings: unknown[];
  chartType: 'candle' | 'area' | 'bar';
}

export type LayoutSyncResult =
  | { kind: 'ok'; etag: string }
  | { kind: 'conflict'; remote: ChartLayout }
  | { kind: 'error'; reason: string };

export async function pushLayout(
  instrumentId: number,
  payload: LayoutPayload,
  expectedEtag: string | null,
  signal?: AbortSignal,
): Promise<LayoutSyncResult> {
  if (signal?.aborted) {
    return { kind: 'error', reason: 'aborted' };
  }

  try {
    const result = await putChartLayout(
      instrumentId,
      { payload, schema_version: 1 },
      expectedEtag ?? '',
      signal,
    );
    return { kind: 'ok', etag: result.updated_at };
  } catch (err) {
    // MED-4: use instanceof EtagMismatchError instead of message string equality.
    if (err instanceof EtagMismatchError) {
      // HIGH-3: thread signal into conflict-path GET (belt-and-suspenders; gen-counter
      // in ChartLayoutSync is the primary stale-result guard).
      const remote = await getChartLayout(instrumentId, signal);
      if (remote === null) {
        return { kind: 'error', reason: 'layout_disappeared' };
      }
      return { kind: 'conflict', remote };
    }
    if (err instanceof Error && err.name === 'AbortError') {
      return { kind: 'error', reason: 'aborted' };
    }
    return {
      kind: 'error',
      reason: err instanceof Error ? err.message : 'unknown',
    };
  }
}
