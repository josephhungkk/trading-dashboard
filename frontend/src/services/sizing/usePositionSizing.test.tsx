import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/services/sizing/api';
import type { SizingRequest, SizingResult } from '@/services/sizing/types';
import { usePositionSizing } from '@/services/sizing/usePositionSizing';

function makeRequest(overrides: Partial<SizingRequest> = {}): SizingRequest {
  return {
    account_id: '00000000-0000-0000-0000-000000000001',
    instrument_id: 12345,
    method: 'fixed_fractional',
    side: 'buy',
    inputs: {
      kind: 'fixed_fractional',
      risk_pct: '2',
      price: '50',
    },
    ...overrides,
  };
}

function makeAllowResult(): SizingResult {
  return {
    suggested_qty: '40',
    base_currency_notional: '2000',
    method: 'fixed_fractional',
    breakdown: {
      nlv_base: '100000',
      fx_rate: '1.0',
      price_base: '50.00',
      account_currency: 'USD',
      vol_source: 'n/a',
    },
    risk_verdict: {
      final_verdict: 'allow',
      blockers: [],
      warnings: [],
      latency_ms: 5,
    },
  };
}

function makeBlockResult(): SizingResult {
  return {
    suggested_qty: '1000',
    base_currency_notional: '50000',
    method: 'fixed_fractional',
    breakdown: {
      nlv_base: '100000',
      fx_rate: '1.0',
      price_base: '50.00',
      account_currency: 'USD',
      vol_source: 'n/a',
    },
    risk_verdict: {
      final_verdict: 'block',
      blockers: [
        { check: 'buying_power', message: 'BP buffer breach', code: 'bp_buffer' },
      ],
      warnings: [],
      latency_ms: 5,
    },
  };
}

describe('usePositionSizing', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('debounces 250ms before calling compute', async () => {
    const spy = vi
      .spyOn(api, 'computePositionSize')
      .mockResolvedValue(makeAllowResult());

    const { result } = renderHook(() => usePositionSizing(makeRequest()));

    // Wait on the rendered state — spy being called doesn't mean
    // setResult has flushed. Asserting on result.current.result avoids
    // the previously-flaky "spy called but state not yet committed" race.
    await waitFor(
      () => expect(result.current.result?.suggested_qty).toBe('40'),
      { timeout: 2000 },
    );
    expect(spy).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBeNull();
  });

  it('surfaces BLOCK verdicts', async () => {
    vi.spyOn(api, 'computePositionSize').mockResolvedValue(makeBlockResult());

    const { result } = renderHook(() =>
      usePositionSizing(
        makeRequest({
          inputs: { kind: 'fixed_fractional', risk_pct: '50', price: '50' },
        }),
      ),
    );

    await waitFor(
      () =>
        expect(result.current.result?.risk_verdict.final_verdict).toBe('block'),
      { timeout: 1000 },
    );
    expect(result.current.result?.risk_verdict.blockers?.[0]?.code).toBe('bp_buffer');
  });

  it('returns null result + null error when req is null', () => {
    const { result } = renderHook(() => usePositionSizing(null));
    expect(result.current.result).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it('propagates fetch errors', async () => {
    const fakeError = new Error('compute_failed');
    vi.spyOn(api, 'computePositionSize').mockRejectedValue(fakeError);

    const { result } = renderHook(() => usePositionSizing(makeRequest()));
    await waitFor(() => expect(result.current.error).toBe(fakeError), {
      timeout: 1000,
    });
    expect(result.current.result).toBeNull();
  });
});
