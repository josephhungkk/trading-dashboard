import * as React from 'react';
 
import { getServices } from '@/services/registry';
 
import type { Quote } from '@/services/types';

export function useTickingQuotes(symbols: readonly string[]): Record<string, Quote | undefined> {
  const [snapshot, setSnapshot] = React.useState<Record<string, Quote | undefined>>(() => {
    const svc = getServices().quotes;
    return Object.fromEntries(symbols.map((s) => [s, svc.getSnapshot(s)]));
  });

  React.useEffect(() => {
    const svc = getServices().quotes;
    let raf = 0;
    let pending: Record<string, Quote> | null = null;
    const flush = (): void => {
      if (!pending) return;
      const p = pending;
      pending = null;
      setSnapshot((prev) => ({ ...prev, ...p }));
    };
    const unsub = svc.subscribe([...symbols], (q: Quote) => {
      if (!pending) pending = {};
      pending[q.symbol] = q;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(flush);
    });
    return () => {
      cancelAnimationFrame(raf);
      unsub();
    };
  }, [symbols]);

  return snapshot;
}
