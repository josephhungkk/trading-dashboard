/**
 * Phase 11a-D — streaming chat websocket hook.
 * Uses bounded reconnect backoff and mountedRef state gates following the
 * portfolio live-rollup hook pattern.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type {
  AICapability,
  ChatMessage,
  ChatWsFrame,
  FallbackHop,
} from '@/services/ai/types';

const RECONNECT_DELAYS_MS = [500, 1_500, 5_000, 15_000];
const RATE_LIMIT_CLEAR_MS = 3_000;

export interface UseChatStreamReturn {
  send: (messages: ChatMessage[], capability: AICapability) => void;
  partial: string;
  done: boolean;
  error: string | null;
  rateLimited: boolean;
  connected: boolean;
  fallbackChain: FallbackHop[];
}

function defaultWsUrl(): string {
  if (typeof window === 'undefined') return '/ws/ai/chat';
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${window.location.host}/ws/ai/chat`;
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

export function useChatStream(opts?: { wsUrl?: string }): UseChatStreamReturn {
  const mountedRef = useRef(true);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rateLimitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);

  const [partial, setPartial] = useState('');
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rateLimited, setRateLimited] = useState(false);
  const [connected, setConnected] = useState(false);
  const [fallbackChain, setFallbackChain] = useState<FallbackHop[]>([]);

  useEffect(() => {
    mountedRef.current = true;
    if (typeof window === 'undefined') return undefined;

    let currentWs: WebSocket | null = null;

    const clearReconnectTimer = (): void => {
      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    const setFallbackFromFrame = (frame: ChatWsFrame): void => {
      if (frame.fallback_chain !== undefined) {
        setFallbackChain(frame.fallback_chain);
      }
    };

    const connect = (): void => {
      if (!mountedRef.current) return;
      const url = opts?.wsUrl ?? defaultWsUrl();
      if (!isSameOriginWsUrl(url)) {
        console.warn('[useChatStream] rejecting non-same-origin wsUrl', url);
        setError('invalid_ws_url');
        return;
      }
      const ws = new WebSocket(url);
      currentWs = ws;
      wsRef.current = ws;
      let downHandled = false;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        attemptRef.current = 0;
        setConnected(true);
      };

      ws.onmessage = (e: MessageEvent<string>) => {
        if (!mountedRef.current) return;
        try {
          const parsedFrame: unknown = JSON.parse(e.data);
          if (typeof parsedFrame !== 'object' || parsedFrame === null) {
            console.warn('[useChatStream] non-object frame, dropping');
            ws.close();
            return;
          }
          if ((parsedFrame as { version?: unknown }).version !== 1) {
            console.warn(
              '[useChatStream] protocol version mismatch — closing',
              (parsedFrame as { version?: unknown }).version,
            );
            setError('protocol_version_mismatch');
            attemptRef.current = RECONNECT_DELAYS_MS.length;
            downHandled = true;
            setConnected(false);
            ws.close();
            return;
          }
          const frame = parsedFrame as ChatWsFrame;
          setFallbackFromFrame(frame);
          if (frame.type === 'chunk') {
            setPartial((current) => current + frame.text);
            return;
          }
          if (frame.type === 'done') {
            setDone(true);
            return;
          }
          setError(frame.message);
          if (frame.error_class === 'TurnRateExceeded') {
            setRateLimited(true);
            if (rateLimitTimerRef.current !== null) {
              clearTimeout(rateLimitTimerRef.current);
            }
            rateLimitTimerRef.current = setTimeout(() => {
              if (mountedRef.current) setRateLimited(false);
            }, RATE_LIMIT_CLEAR_MS);
          }
        } catch (err) {
          console.warn('[useChatStream] malformed frame, closing socket', err);
          ws.close();
        }
      };

      const onDown = (): void => {
        if (!mountedRef.current || downHandled) return;
        downHandled = true;
        setConnected(false);
        const delay = RECONNECT_DELAYS_MS[attemptRef.current];
        if (delay !== undefined) {
          attemptRef.current += 1;
          clearReconnectTimer();
          reconnectTimerRef.current = setTimeout(connect, delay);
        } else {
          console.warn('[useChatStream] reconnect exhausted');
          setError('connection_failed');
        }
      };

      ws.onclose = onDown;
      ws.onerror = onDown;
    };

    connect();

    return () => {
      mountedRef.current = false;
      clearReconnectTimer();
      if (rateLimitTimerRef.current !== null) {
        clearTimeout(rateLimitTimerRef.current);
        rateLimitTimerRef.current = null;
      }
      if (currentWs !== null) currentWs.close();
      wsRef.current = null;
    };
  }, [opts?.wsUrl]);

  const send = useCallback((messages: ChatMessage[], capability: AICapability): void => {
    setPartial('');
    setDone(false);
    setError(null);
    setFallbackChain([]);

    const ws = wsRef.current;
    if (ws?.readyState !== WebSocket.OPEN) {
      setError('websocket_unavailable');
      return;
    }
    ws.send(JSON.stringify({ messages, capability }));
  }, []);

  return {
    send,
    partial,
    done,
    error,
    rateLimited,
    connected,
    fallbackChain,
  };
}
