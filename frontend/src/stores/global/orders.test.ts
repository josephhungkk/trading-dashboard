import { beforeEach, describe, expect, it } from 'vitest';
import { useOrdersStore, type OrderEventLike, type OrderResponse } from './orders';

const order = (overrides: Partial<OrderResponse> = {}): OrderResponse => ({
  id: 'order-1',
  last_event_at: '2026-04-27T09:00:00Z',
  status: 'SUBMITTED',
  quantity: '10',
  ...overrides,
});

describe('useOrdersStore', () => {
  beforeEach(() => {
    useOrdersStore.getState().clear();
  });

  it('addOrder_inserts_by_id', () => {
    const o1 = order();

    useOrdersStore.getState().addOrder(o1);

    expect(useOrdersStore.getState().orders[o1.id]).toBe(o1);
  });

  it('applyEvent_updates_existing', () => {
    useOrdersStore.getState().addOrder(order());
    const event: OrderEventLike = {
      id: 'order-1',
      last_event_at: '2026-04-27T09:01:00Z',
      status: 'FILLED',
    };

    useOrdersStore.getState().applyEvent(event);

    expect(useOrdersStore.getState().orders['order-1']).toMatchObject({
      id: 'order-1',
      last_event_at: '2026-04-27T09:01:00Z',
      status: 'FILLED',
      quantity: '10',
    });
  });

  it('applyEvent_skips_older_events', () => {
    const current = order({
      last_event_at: '2026-04-27T09:05:00Z',
      status: 'FILLED',
    });
    useOrdersStore.getState().addOrder(current);

    useOrdersStore.getState().applyEvent({
      id: 'order-1',
      last_event_at: '2026-04-27T09:04:59Z',
      status: 'SUBMITTED',
    });

    expect(useOrdersStore.getState().orders['order-1']).toBe(current);
  });

  it('applyEvent_inserts_unknown_order', () => {
    const event: OrderEventLike = {
      id: 'order-2',
      last_event_at: '2026-04-27T09:10:00Z',
      status: 'SUBMITTED',
    };

    useOrdersStore.getState().applyEvent(event);

    expect(useOrdersStore.getState().orders['order-2']).toEqual(event);
  });

  it('setKillSwitchActive_toggles', () => {
    useOrdersStore.getState().setKillSwitchActive(true);
    expect(useOrdersStore.getState().killSwitchActive).toBe(true);

    useOrdersStore.getState().setKillSwitchActive(false);
    expect(useOrdersStore.getState().killSwitchActive).toBe(false);
  });

  it('clear_resets', () => {
    useOrdersStore.getState().addOrder(order());
    useOrdersStore.getState().setKillSwitchActive(true);
    useOrdersStore.getState().setBrokerMaintenance({
      active: true,
      window: 'daily',
      until: '2026-04-27T10:00:00Z',
    });

    useOrdersStore.getState().clear();

    expect(useOrdersStore.getState().orders).toEqual({});
    expect(useOrdersStore.getState().killSwitchActive).toBe(false);
    expect(useOrdersStore.getState().brokerMaintenance).toBeNull();
  });
});
