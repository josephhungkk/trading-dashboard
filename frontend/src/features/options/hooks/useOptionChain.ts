import * as React from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchChain } from '@/services/options/api';
import type { OptionChainData, WsChainFrame } from '@/features/options/types';

function buildWsUrl(symbol: string, expiry: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/ws/options/chain?symbol=${encodeURIComponent(symbol)}&expiry=${expiry}`;
}

export function useOptionChain(symbol: string, expiry: string | null, currency = 'USD') {
  const queryClient = useQueryClient();
  const queryKey = React.useMemo(
    () => ['options', 'chain', symbol, expiry, currency] as const,
    [symbol, expiry, currency],
  );

  const query = useQuery({
    queryKey,
    queryFn: () => (expiry ? fetchChain(symbol, expiry, 20, currency) : Promise.resolve(null)),
    enabled: symbol.trim().length > 0 && expiry !== null,
    staleTime: 30_000,
    refetchInterval: 5_000,
  });

  const conidMapRef = React.useRef<Map<string, string>>(new Map());
  const wsRef = React.useRef<WebSocket | null>(null);
  const [wsStale, setWsStale] = React.useState(false);
  const [conidMap, setConidMap] = React.useState<Map<string, string>>(new Map());

  React.useEffect(() => {
    if (!symbol || !expiry) return;

    let reconnectDelay = 500;
    let cancelled = false;

    function connect() {
      if (cancelled) return;
      const ws = new WebSocket(buildWsUrl(symbol, expiry ?? ''));
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const frame = JSON.parse(event.data as string) as WsChainFrame;
          if (frame.type === 'chain' && frame.calls !== undefined) {
            setWsStale(false);
            queryClient.setQueryData<OptionChainData>(queryKey, {
              calls: frame.calls ?? [],
              puts: frame.puts ?? [],
              source: frame.source ?? '',
              fetched_at_ms: frame.fetched_at_ms ?? Date.now(),
            });
          } else if (frame.type === 'stale') {
            setWsStale(true);
          } else if (frame.type === 'canonicalized' && frame.conid && frame.canonical_id) {
            conidMapRef.current.set(frame.conid, frame.canonical_id);
            setConidMap(new Map(conidMapRef.current));
          }
        } catch {
          // ignore malformed frames
        }
      };

      ws.onclose = () => {
        if (!cancelled) {
          setTimeout(connect, Math.min(reconnectDelay, 15_000));
          reconnectDelay = Math.min(reconnectDelay * 1.5, 15_000);
        }
      };
    }

    connect();
    return () => {
      cancelled = true;
      wsRef.current?.close();
    };
  }, [symbol, expiry, currency, queryClient, queryKey]);

  return { ...query, wsStale, conidMap };
}
