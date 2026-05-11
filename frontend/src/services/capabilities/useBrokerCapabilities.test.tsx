import * as React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useBrokerCapabilities } from './useBrokerCapabilities';
import type { BrokerCapabilitiesResponse } from './types';

class MockEventSource {
  static instances: MockEventSource[] = [];

  readonly url: string;
  private listener: (() => void) | null = null;
  close = vi.fn();

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener): void {
    if (type === 'message') {
      this.listener = () => listener(new MessageEvent('message'));
    }
  }

  removeEventListener(type: string): void {
    if (type === 'message') this.listener = null;
  }

  emitMessage(): void {
    this.listener?.();
  }
}

const originalEventSource = globalThis.EventSource;

const capabilities: BrokerCapabilitiesResponse = {
  broker_id: 'schwab',
  order_types: [
    { code: 'MARKET', label: 'Market', description: 'Market order', sort_order: 10 },
    { code: 'LIMIT', label: 'Limit', description: 'Limit order', sort_order: 20 },
    { code: 'TRAIL', label: 'Trailing stop', description: 'Trailing stop order', sort_order: 50 },
  ],
  time_in_force: [
    { code: 'DAY', label: 'Day', description: 'Day order', requires_expiry: false, sort_order: 10 },
    { code: 'GTC', label: 'GTC', description: 'Good til canceled', requires_expiry: false, sort_order: 20 },
  ],
  combos: [
    { broker_id: 'schwab', asset_class: 'STOCK', order_type: 'MARKET', time_in_force: 'DAY', supported: true, notes: '' },
    { broker_id: 'schwab', asset_class: 'STOCK', order_type: 'LIMIT', time_in_force: 'GTC', supported: true, notes: '' },
    { broker_id: 'schwab', asset_class: 'STOCK', order_type: 'TRAIL', time_in_force: 'DAY', supported: false, notes: 'Not supported by Schwab' },
  ],
};

function wrapper(client: QueryClient): React.FC<{ children: React.ReactNode }> {
  return function HookWrapper({ children }: { children: React.ReactNode }): React.JSX.Element {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function jsonResponse(body: BrokerCapabilitiesResponse): Response {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  } as Response;
}

describe('useBrokerCapabilities', () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    globalThis.EventSource = MockEventSource as unknown as typeof EventSource;
  });

  afterEach(() => {
    globalThis.EventSource = originalEventSource;
    vi.restoreAllMocks();
  });

  it('returns isSupported=true for combos in the capabilities response', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => Promise.resolve(jsonResponse(capabilities)));
    const client = new QueryClient();

    const { result } = renderHook(() => useBrokerCapabilities('schwab'), { wrapper: wrapper(client) });

    await waitFor(() => expect(result.current.isSupported('MARKET', 'DAY')).toBe(true));
    expect(result.current.isSupported('LIMIT', 'GTC')).toBe(true);
  });

  it('returns isSupported=false for combos not in the capabilities response', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => Promise.resolve(jsonResponse(capabilities)));
    const client = new QueryClient();

    const { result } = renderHook(() => useBrokerCapabilities('schwab'), { wrapper: wrapper(client) });

    await waitFor(() => expect(result.current.isSupported('MARKET', 'DAY')).toBe(true));
    expect(result.current.isSupported('MARKET', 'GTC')).toBe(false);
    expect(result.current.notesFor('TRAIL', 'DAY')).toBe('Not supported by Schwab');
  });

  it('returns isSupported=false and does not fetch when brokerId is null', () => {
    const fetchMock = vi.fn();
    vi.spyOn(globalThis, 'fetch').mockImplementation(fetchMock);
    const client = new QueryClient();

    const { result } = renderHook(() => useBrokerCapabilities(null), { wrapper: wrapper(client) });

    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.isSupported('MARKET', 'DAY')).toBe(false);
  });
});
