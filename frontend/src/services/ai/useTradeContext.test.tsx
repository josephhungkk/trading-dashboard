import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/services/ai/api';
import type { CompletionResult } from '@/services/ai/types';
import { useTradeContext } from '@/services/ai/useTradeContext';

function completion(text: string): CompletionResult {
  return {
    completion_tokens: 12,
    fallback_chain: [],
    model: 'qwen3',
    prompt_tokens: 24,
    provider: 'local',
    request_id: '00000000-0000-4000-8000-000000000001',
    text,
    wall_time_ms: 50,
  };
}

describe('useTradeContext', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('populates context from JSON text', async () => {
    const postComplete = vi.spyOn(api, 'postComplete').mockResolvedValue(
      completion('{"summary":"Momentum intact","recent_signals":[],"risk_flags":[]}'),
    );

    const { result } = renderHook(() =>
      useTradeContext({ symbol: 'AAPL', side: 'BUY', qty: 10 }),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.context).toEqual({
      summary: 'Momentum intact',
      recent_signals: [],
      risk_flags: [],
    });
    expect(result.current.error).toBeNull();
    expect(postComplete).toHaveBeenCalledWith(
      expect.objectContaining({ capability: 'STRUCTURED_OUTPUT' }),
    );
  });

  it('gracefully degrades when the AI proxy is unavailable', async () => {
    const err = new Error('ai api 503') as api.AiApiError;
    err.status = 503;
    err.payload = { detail: 'unavailable' };
    vi.spyOn(api, 'postComplete').mockRejectedValue(err);

    const { result } = renderHook(() =>
      useTradeContext({ symbol: 'MSFT', side: 'SELL', qty: 5 }),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.context).toBeNull();
    expect(result.current.error).toBe('unavailable');
  });

  it('gracefully degrades when the AI proxy rate limits', async () => {
    const err = new Error('ai api 429') as api.AiApiError;
    err.status = 429;
    err.payload = { detail: 'rate limited' };
    vi.spyOn(api, 'postComplete').mockRejectedValue(err);

    const { result } = renderHook(() =>
      useTradeContext({ symbol: 'NVDA', side: 'BUY', qty: 2 }),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.context).toBeNull();
    expect(result.current.error).toBe('rate_limited');
  });

  it('gracefully degrades on request errors', async () => {
    const err = new Error('ai api 400') as api.AiApiError;
    err.status = 400;
    err.payload = { detail: 'bad request' };
    vi.spyOn(api, 'postComplete').mockRejectedValue(err);

    const { result } = renderHook(() =>
      useTradeContext({ symbol: 'MSFT', side: 'SELL', qty: 5 }),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.context).toBeNull();
    expect(result.current.error).toBe('request_error');
  });

  it('gracefully degrades on malformed JSON', async () => {
    vi.spyOn(api, 'postComplete').mockResolvedValue(completion('not json'));

    const { result } = renderHook(() =>
      useTradeContext({ symbol: 'TSLA', side: 'BUY', qty: 1 }),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.context).toBeNull();
    expect(result.current.error).toBe('parse_failed');
  });
});
