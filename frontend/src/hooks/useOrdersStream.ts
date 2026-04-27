import { useEffect } from 'react';
import type { OrderEventLike } from '@/stores/global/orders';
import { useOrdersStore } from '@/stores/global/orders';

const INITIAL_RECONNECT_MS = 1_000;
const MAX_RECONNECT_MS = 30_000;

function ordersEventsUrl(accountId?: string): string {
  const params = new URLSearchParams();
  if (accountId) params.set('account_id', accountId);

  const { lastEventId } = useOrdersStore.getState() as ReturnType<typeof useOrdersStore.getState> & {
    lastEventId?: string | null;
  };
  if (lastEventId) params.set('last_event_id', lastEventId);

  const query = params.toString();
  return query ? `/api/orders/events?${query}` : '/api/orders/events';
}

export function useOrdersStream(accountId?: string): void {
  useEffect(() => {
    let eventSource: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectDelay = INITIAL_RECONNECT_MS;
    let disposed = false;

    const connect = () => {
      if (disposed) return;

      eventSource = new EventSource(ordersEventsUrl(accountId));
      eventSource.onopen = () => {
        reconnectDelay = INITIAL_RECONNECT_MS;
      };
      eventSource.onmessage = (event) => {
        const parsed = JSON.parse(event.data) as OrderEventLike;
        useOrdersStore.getState().applyEvent(parsed);
      };
      eventSource.onerror = () => {
        eventSource?.close();
        if (disposed) return;

        reconnectTimer = setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_MS);
      };
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      eventSource?.close();
    };
  }, [accountId]);
}
