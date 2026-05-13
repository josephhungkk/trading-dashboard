/**
 * Phase 11a-D — one-shot AI context for trade tickets.
 * Failure is deliberately non-blocking: callers always receive a nullable
 * context plus an error code instead of a thrown exception.
 */

import { useEffect, useMemo, useState } from 'react';

import { isAiApiError, postComplete } from '@/services/ai/api';
import type { CompletionRequest } from '@/services/ai/types';

export interface TradeContext {
  summary: string;
  recent_signals: string[];
  risk_flags: string[];
}

export interface UseTradeContextReturn {
  context: TradeContext | null;
  loading: boolean;
  error: string | null;
}

export interface TradeContextInput {
  symbol: string;
  side: 'BUY' | 'SELL';
  qty: number;
}

function buildPrompt(input: TradeContextInput): string {
  return [
    'Return JSON only for this trade ticket context.',
    'Use exactly: {"summary": string, "recent_signals": string[], "risk_flags": string[]}.',
    `Symbol: ${input.symbol}`,
    `Side: ${input.side}`,
    `Quantity: ${input.qty}`,
  ].join('\n');
}

function buildRequest(input: TradeContextInput): CompletionRequest {
  return {
    caller: 'trade-ticket-ai-context',
    capability: 'STRUCTURED_OUTPUT',
    force_local_only: false,
    max_tokens: 512,
    messages: [
      {
        role: 'system',
        content: 'You produce compact, valid JSON for a trading UI.',
      },
      {
        role: 'user',
        content: buildPrompt(input),
      },
    ],
    response_format: { type: 'json_object' },
    temperature: 0.1,
    tools: null,
  };
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function parseTradeContext(text: string): TradeContext | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return null;
  }
  const candidate = parsed as {
    summary?: unknown;
    recent_signals?: unknown;
    risk_flags?: unknown;
  };
  if (
    typeof candidate.summary !== 'string'
    || !isStringArray(candidate.recent_signals)
    || !isStringArray(candidate.risk_flags)
  ) {
    return null;
  }
  return {
    summary: candidate.summary,
    recent_signals: candidate.recent_signals,
    risk_flags: candidate.risk_flags,
  };
}

function errorCode(err: unknown): string {
  if (isAiApiError(err) && err.status >= 500) return 'unavailable';
  return 'unavailable';
}

export function useTradeContext(input: TradeContextInput): UseTradeContextReturn {
  const { qty, side, symbol } = input;
  const request = useMemo(
    () => buildRequest({ qty, side, symbol }),
    [qty, side, symbol],
  );
  const [state, setState] = useState<UseTradeContextReturn>({
    context: null,
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      setState({ context: null, loading: true, error: null });
      try {
        const result = await postComplete(request);
        if (cancelled) return;
        const context = parseTradeContext(result.text);
        if (context === null) {
          setState({ context: null, loading: false, error: 'parse_failed' });
          return;
        }
        setState({ context, loading: false, error: null });
      } catch (err) {
        if (cancelled) return;
        setState({ context: null, loading: false, error: errorCode(err) });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [request]);

  return state;
}
