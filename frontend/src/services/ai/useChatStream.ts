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
      const ws = new WebSocket(opts?.wsUrl ?? defaultWsUrl());
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
          const frame = JSON.parse(e.data) as ChatWsFrame;
          if (frame.version !== 1) {
            ws.close();
            return;
          }
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
        } catch {
          // Malformed frames are ignored; callers can send again manually.
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
