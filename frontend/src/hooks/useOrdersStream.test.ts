import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { OrderEventLike } from '@/stores/global/orders';
import { useOrdersStore } from '@/stores/global/orders';
import { useOrdersStream } from './useOrdersStream';

class MockEventSource {
  static instances: MockEventSource[] = [];

  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;
  close = vi.fn();

  constructor(public readonly url: string) {
    MockEventSource.instances.push(this);
  }
}

describe('useOrdersStream', () => {
  const originalEventSource = globalThis.EventSource;
  const applyEvent = vi.fn();
  const originalApplyEvent = useOrdersStore.getState().applyEvent;

  beforeEach(() => {
    vi.clearAllMocks();
    MockEventSource.instances = [];
    globalThis.EventSource = MockEventSource as unknown as typeof EventSource;
    useOrdersStore.setState({ applyEvent });
  });

  afterEach(() => {
    globalThis.EventSource = originalEventSource;
    useOrdersStore.setState({ applyEvent: originalApplyEvent });
  });

  it('opens_eventsource_on_mount', () => {
    renderHook(() => useOrdersStream());

    expect(MockEventSource.instances[0]?.url).toBe('/api/orders/events');
  });

  it('passes_account_id_query_param', () => {
    renderHook(() => useOrdersStream('acc-123'));

    expect(MockEventSource.instances[0]?.url).toContain('account_id=acc-123');
  });

  it('pipes_events_into_store', () => {
    renderHook(() => useOrdersStream());
    const payload: OrderEventLike = {
      id: 'ord-1',
      last_event_at: '2026-04-27T08:01:00Z',
      type: 'order.updated',
    };

    MockEventSource.instances[0]?.onmessage?.(
      new MessageEvent('message', { data: JSON.stringify(payload) }),
    );

    expect(applyEvent).toHaveBeenCalledWith(payload);
  });

  it('closes_eventsource_on_unmount', () => {
    const { unmount } = renderHook(() => useOrdersStream());
    const eventSource = MockEventSource.instances[0];

    unmount();

    expect(eventSource?.close).toHaveBeenCalledTimes(1);
  });
});
