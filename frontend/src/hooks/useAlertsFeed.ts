/**
 * Phase 11b-D3 — alerts WS feed with reconnect backfill via last_seen_at.
 * Mirrors services/ai/useChatStream.ts bounded backoff + same-origin guard.
 */

import { useEffect, useRef, useState } from 'react';

import { useAlertsStore } from '@/stores/global/alerts';

import { getRecentFires } from '@/services/alerts/api';
import type { AlertWsFrame } from '@/services/alerts/types';

const RECONNECT_DELAYS_MS = [500, 1_500, 5_000, 15_000];

function defaultWsUrl(): string {
  if (typeof window === 'undefined') return '/ws/alerts/feed';
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${window.location.host}/ws/alerts/feed`;
}

function isSameOriginWsUrl(url: string): boolean {
  if (typeof window === 'undefined') return true;
  try {
    const parsed = new URL(url, window.location.href);
    return (
      (parsed.protocol === 'ws:' || parsed.protocol === 'wss:')
      && parsed.host === window.location.host
    );
  } catch {
    return false;
  }
}

export interface UseAlertsFeedState {
  connected: boolean;
  error: string | null;
}

export function useAlertsFeed(opts?: { wsUrl?: string }): UseAlertsFeedState {
  const mountedRef = useRef(true);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    if (typeof window === 'undefined') return undefined;

    const clearReconnectTimer = (): void => {
      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    const backfillThenConnect = async (): Promise<void> => {
      if (!mountedRef.current) return;
      const { lastSeenAt, mergeFires } = useAlertsStore.getState();
      try {
        const { fires } = await getRecentFires(lastSeenAt, 50);
        if (mountedRef.current) mergeFires(fires);
      } catch (err) {
        console.warn('[useAlertsFeed] backfill failed', err);
      }
      if (!mountedRef.current) return;

      const url = opts?.wsUrl ?? defaultWsUrl();
      if (!isSameOriginWsUrl(url)) {
        console.warn('[useAlertsFeed] rejecting non-same-origin wsUrl', url);
        setError('invalid_ws_url');
        return;
      }
      const ws = new WebSocket(url);
      wsRef.current = ws;
      let downHandled = false;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        attemptRef.current = 0;
        setConnected(true);
        setError(null);
      };

      ws.onmessage = (e: MessageEvent<string>) => {
        if (!mountedRef.current) return;
        try {
          const parsed: unknown = JSON.parse(e.data);
          if (typeof parsed !== 'object' || parsed === null) {
            ws.close();
            return;
          }
          const frame = parsed as Partial<AlertWsFrame>;
          if (frame.v !== 1) {
            console.warn('[useAlertsFeed] protocol version mismatch', frame.v);
            ws.close();
            return;
          }
          if (frame.type === 'fire'
            && typeof frame.fire_id === 'number'
            && typeof frame.alert_id === 'number'
            && typeof frame.fired_at === 'string'
            && typeof frame.verdict === 'string'
          ) {
            useAlertsStore.getState().appendFire({
              id: frame.fire_id,
              alert_id: frame.alert_id,
              fired_at: frame.fired_at,
              verdict: frame.verdict,
            });
          }
        } catch (err) {
          console.warn('[useAlertsFeed] malformed frame, closing', err);
          ws.close();
        }
      };

      const onDown = (): void => {
        if (!mountedRef.current || downHandled) return;
        downHandled = true;
        setConnected(false);
        const delay = RECONNECT_DELAYS_MS[attemptRef.current];
        if (delay === undefined) {
          setError('reconnect_exhausted');
          return;
        }
        attemptRef.current += 1;
        clearReconnectTimer();
        reconnectTimerRef.current = setTimeout(() => {
          void backfillThenConnect();
        }, delay);
      };
      ws.onclose = onDown;
      ws.onerror = onDown;
    };

    void backfillThenConnect();

    return () => {
      mountedRef.current = false;
      clearReconnectTimer();
      const ws = wsRef.current;
      if (ws !== null) ws.close();
      wsRef.current = null;
    };
  }, [opts?.wsUrl]);

  return { connected, error };
}
