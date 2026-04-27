import { useCallback, useState } from 'react';
import { BrokerMaintenanceError, getOrders } from '@/services/orders';
import { MaintenanceError } from '@/services/errors';
import type { BrokerMaintenance, Order, OrderResponse } from '@/services/types';
import { useFleetMaintenance } from '@/stores/global/fleet-maintenance';
import { useOrdersStore, type BrokerMaintenance as StoreBrokerMaintenance, type OrderResponse as StoreOrderResponse } from '@/stores/global/orders';

function normalizeMaintenance(maintenance: BrokerMaintenance): StoreBrokerMaintenance {
  return {
    active: maintenance.active,
    window: maintenance.window ?? null,
    until: maintenance.until ?? null,
  };
}

function syncMaintenance(maintenance: BrokerMaintenance): void {
  const normalized = normalizeMaintenance(maintenance);
  useOrdersStore.getState().setBrokerMaintenance(normalized);
  useFleetMaintenance.getState().setMaintenance({
    active: normalized.active,
    window: normalized.window,
    until: normalized.until ? new Date(normalized.until) : null,
  });
}

function isMaintenanceError(error: unknown): error is MaintenanceError {
  return error instanceof MaintenanceError || (
    error instanceof Error &&
    error.name === 'MaintenanceError' &&
    'window' in error &&
    'until' in error
  );
}

function isOrderResponse(order: OrderResponse | Order): order is OrderResponse {
  return 'account_id' in order;
}

function normalizeOrder(order: OrderResponse | Order): StoreOrderResponse {
  if (isOrderResponse(order)) {
    return {
      ...order,
      last_event_at: order.last_event_at ?? order.updated_at,
    };
  }

  return {
    ...order,
    last_event_at: order.updatedAt,
  };
}

function syncOrders(orders: (OrderResponse | Order)[]): void {
  const store = useOrdersStore.getState();
  store.clear();
  for (const order of orders) store.addOrder(normalizeOrder(order));
}

export function useOrdersList(): {
  fetchAndSync: (opts?: { status?: string }) => Promise<void>;
  isLoading: boolean;
  error: Error | null;
} {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const fetchAndSync = useCallback(async (opts?: { status?: string }) => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await getOrders(opts);
      syncOrders(response.orders);
      if (response.brokerMaintenance) syncMaintenance(response.brokerMaintenance);
      useOrdersStore.getState().setKillSwitchActive(response.killSwitchActive ?? false);
    } catch (caught) {
      const nextError = caught instanceof Error ? caught : new Error(String(caught));
      setError(nextError);

      if (caught instanceof BrokerMaintenanceError) {
        if (caught.brokerMaintenance) syncMaintenance(caught.brokerMaintenance);
        return;
      }

      if (isMaintenanceError(caught)) {
        syncMaintenance({
          active: true,
          window: caught.window,
          until: caught.until || null,
        });
        return;
      }

      throw caught;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return { fetchAndSync, isLoading, error };
}
