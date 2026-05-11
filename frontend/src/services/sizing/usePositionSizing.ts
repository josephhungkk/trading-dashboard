import { useEffect, useRef, useState } from 'react';

import { computePositionSize } from '@/services/sizing/api';
import type { SizingRequest, SizingResult } from '@/services/sizing/types';

const DEBOUNCE_MS = 250;

export interface UsePositionSizingState {
  result: SizingResult | null;
  error: Error | null;
  loading: boolean;
}

/**
 * Debounced computed-sizing hook. Returns the latest SizingResult for the
 * given request, recomputing 250ms after the last input change. Returns
 * null result + null error before the first successful response.
 */
export function usePositionSizing(req: SizingRequest | null): UsePositionSizingState {
  const [result, setResult] = useState<SizingResult | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Stable string key for the request — JSON.stringify is good enough
  // here because the shape is small and rendered once per request change.
  const key = req ? JSON.stringify(req) : null;

  useEffect(() => {
    if (!req) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    let cancelled = false;
    timerRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const r = await computePositionSize(req);
        if (!cancelled) {
          setResult(r);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e as Error);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
    // key is a stable identity for `req` — including `req` itself would
    // re-fire on every render due to inline object identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // When the caller drops the request, return null result without
  // calling setState — avoids the react-hooks/set-state-in-effect rule
  // and avoids a re-render. The previous result is intentionally hidden,
  // not erased; the next non-null req will overwrite it.
  if (!req) {
    return { result: null, error: null, loading: false };
  }

  return { result, error, loading };
}
