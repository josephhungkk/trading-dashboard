/**
 * Phase 11b-D3 — alerts WS feed with reconnect backfill via last_seen_at.
 * Mirrors services/ai/useChatStream.ts bounded backoff + same-origin guard.
 */

import { useEffect, useState } from 'react';

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
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;

    // Per-effect-generation cancellation flag — Codex chunk-D MED:
    // a shared mountedRef across effect re-runs (Strict Mode replay, wsUrl
    // change mid-backfill) could let an old backfill resume after the next
    // setup, opening a second WebSocket. A local `cancelled` closure scoped
    // to this useEffect run is the standard React idiom.
    let cancelled = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    const clearReconnectTimer = (): void => {
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };

    const backfillThenConnect = async (): Promise<void> => {
      if (cancelled) return;
      const { lastSeenAt, mergeFires } = useAlertsStore.getState();
      try {
        const { fires } = await getRecentFires(lastSeenAt, 50);
        if (!cancelled) mergeFires(fires);
      } catch (err) {
        console.warn('[useAlertsFeed] backfill failed', err);
      }
      if (cancelled) return;

      const url = opts?.wsUrl ?? defaultWsUrl();
      if (!isSameOriginWsUrl(url)) {
        console.warn('[useAlertsFeed] rejecting non-same-origin wsUrl', url);
        setError('invalid_ws_url');
        return;
      }
      const socket = new WebSocket(url);
      ws = socket;
      let downHandled = false;

      socket.onopen = () => {
        if (cancelled) return;
        attempt = 0;
        setConnected(true);
        setError(null);
      };

      socket.onmessage = (e: MessageEvent<string>) => {
        if (cancelled) return;
        try {
          const parsed: unknown = JSON.parse(e.data);
          if (typeof parsed !== 'object' || parsed === null) {
            socket.close();
            return;
          }
          const frame = parsed as Partial<AlertWsFrame>;
          if (frame.v !== 1) {
            console.warn('[useAlertsFeed] protocol version mismatch', frame.v);
            socket.close();
            return;
          }
          // Codex chunk-D MED — structurally malformed v=1 frames must close
          // the socket to trigger reconnect/backfill, not silently drop.
          // Only `type: 'fire'` is defined in §10; missing required fields
          // means a misbehaving server.
          if (
            frame.type !== 'fire'
            || typeof frame.fire_id !== 'number'
            || typeof frame.alert_id !== 'number'
            || typeof frame.fired_at !== 'string'
            || typeof frame.verdict !== 'string'
          ) {
            console.warn('[useAlertsFeed] malformed v=1 frame, closing');
            socket.close();
            return;
          }
          useAlertsStore.getState().appendFire({
            id: frame.fire_id,
            alert_id: frame.alert_id,
            fired_at: frame.fired_at,
            verdict: frame.verdict,
          });
        } catch (err) {
          console.warn('[useAlertsFeed] malformed frame, closing', err);
          socket.close();
        }
      };

      const onDown = (): void => {
        if (cancelled || downHandled) return;
        downHandled = true;
        setConnected(false);
        const delay = RECONNECT_DELAYS_MS[attempt];
        if (delay === undefined) {
          setError('reconnect_exhausted');
          return;
        }
        attempt += 1;
        clearReconnectTimer();
        reconnectTimer = setTimeout(() => {
          void backfillThenConnect();
        }, delay);
      };
      socket.onclose = onDown;
      socket.onerror = onDown;
    };

    void backfillThenConnect();

    return () => {
      cancelled = true;
      clearReconnectTimer();
      if (ws !== null) ws.close();
      ws = null;
    };
  }, [opts?.wsUrl]);

  return { connected, error };
}
