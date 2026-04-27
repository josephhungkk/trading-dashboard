import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BrokerMaintenanceError } from '@/services/orders';
import type { DecimalString, OrderResponse } from '@/services/types';
import type { OrderResponse as StoreOrderResponse } from '@/stores/global/orders';
import { useOrdersStore } from '@/stores/global/orders';
import { useOrdersList } from './useOrdersList';

vi.mock('@/services/orders', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/orders')>();
  return {
    ...actual,
    getOrders: vi.fn(),
  };
});

import { getOrders } from '@/services/orders';

const mockGetOrders = vi.mocked(getOrders);

const decimal = (value: string): DecimalString => value as DecimalString;

const order: OrderResponse = {
  id: 'ord-1',
  account_id: 'acc-1',
  broker_order_id: null,
  symbol: 'AAPL',
  side: 'BUY',
  order_type: 'LIMIT',
  tif: 'DAY',
  qty: decimal('10'),
  limit_price: decimal('100'),
  stop_price: null,
  status: 'submitted',
  filled_qty: decimal('0'),
  avg_fill_price: null,
  notional: decimal('1000'),
  created_at: '2026-04-27T08:00:00Z',
  updated_at: '2026-04-27T08:00:00Z',
  last_event_at: '2026-04-27T08:00:00Z',
  submission_state: 'submitted',
  events: [],
};

const storeOrder: StoreOrderResponse = {
  ...order,
  last_event_at: order.last_event_at ?? order.updated_at,
};

describe('useOrdersList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useOrdersStore.getState().clear();
  });

  it('fetchAndSync_publishes_orders_and_maintenance_to_store', async () => {
    const brokerMaintenance = {
      active: true,
      window: 'daily' as const,
      until: '2026-04-27T20:00:00Z',
    };
    const addOrder = vi.spyOn(useOrdersStore.getState(), 'addOrder');
    const setBrokerMaintenance = vi.spyOn(useOrdersStore.getState(), 'setBrokerMaintenance');
    mockGetOrders.mockResolvedValueOnce({
      orders: [order],
      brokerMaintenance,
      killSwitchActive: true,
    });

    const { result } = renderHook(() => useOrdersList());

    await act(async () => {
      await result.current.fetchAndSync();
    });

    expect(addOrder).toHaveBeenCalledWith(order);
    expect(setBrokerMaintenance).toHaveBeenCalledWith(brokerMaintenance);
    expect(useOrdersStore.getState().orders).toEqual({ [order.id]: storeOrder });
    expect(useOrdersStore.getState().killSwitchActive).toBe(true);
    expect(result.current.error).toBeNull();
    expect(result.current.isLoading).toBe(false);
  });

  it('503_does_not_clear_orders', async () => {
    useOrdersStore.setState({ orders: { [order.id]: storeOrder } });
    const addOrder = vi.spyOn(useOrdersStore.getState(), 'addOrder');
    mockGetOrders.mockRejectedValueOnce(new BrokerMaintenanceError('60', {
      active: true,
      window: 'daily',
      until: '2026-04-27T20:00:00Z',
    }));

    const { result } = renderHook(() => useOrdersList());

    await act(async () => {
      await result.current.fetchAndSync();
    });

    expect(addOrder).not.toHaveBeenCalled();
    expect(useOrdersStore.getState().orders).toEqual({ [order.id]: storeOrder });
    expect(useOrdersStore.getState().brokerMaintenance).toEqual({
      active: true,
      window: 'daily',
      until: '2026-04-27T20:00:00Z',
    });
  });
});
