import { useCallback, useEffect, useRef, useState } from 'react';

import {
  getTokenStatus,
  subscribeConfigStream,
  type SchwabTokenStatus,
} from '@/services/schwab';

const SLOW_POLL_MS = 60_000;
const FAST_POLL_MS = 5_000;
const FAST_POLL_DURATION_MS = 60_000;

export function useSchwabTokenStatus(opts: { fetchFn?: typeof fetch } = {}) {
  const [status, setStatus] = useState<SchwabTokenStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const intervalRef = useRef<number | null>(null);
  const fastUntilRef = useRef<number>(0);
  const fetchFnRef = useRef<typeof fetch | undefined>(opts.fetchFn);

  useEffect(() => {
    fetchFnRef.current = opts.fetchFn;
  }, [opts.fetchFn]);

  const refetch = useCallback(async () => {
    try {
      const next = await getTokenStatus(fetchFnRef.current);
      setStatus(next);
      setError(null);
    } catch (e) {
      setError(e as Error);
    } finally {
      setLoading(false);
    }
  }, []);

  const scheduleNext = useCallback(() => {
    const tick = (): void => {
      if (intervalRef.current !== null) {
        clearTimeout(intervalRef.current);
      }
      const ms = Date.now() < fastUntilRef.current ? FAST_POLL_MS : SLOW_POLL_MS;
      intervalRef.current = window.setTimeout(async () => {
        await refetch();
        tick();
      }, ms);
    };
    tick();
  }, [refetch]);

  const startFastPoll = useCallback(() => {
    fastUntilRef.current = Date.now() + FAST_POLL_DURATION_MS;
    scheduleNext();
  }, [scheduleNext]);

  useEffect(() => {
    let cancelled = false;
    const init = async (): Promise<void> => {
      await refetch();
      if (!cancelled) {
        scheduleNext();
      }
    };
    void init();
    const unsubscribe = subscribeConfigStream('schwab', () => {
      void refetch();
    });
    return () => {
      cancelled = true;
      unsubscribe();
      if (intervalRef.current !== null) {
        clearTimeout(intervalRef.current);
      }
    };
  }, [refetch, scheduleNext]);

  return { status, loading, error, refetch, startFastPoll };
}
