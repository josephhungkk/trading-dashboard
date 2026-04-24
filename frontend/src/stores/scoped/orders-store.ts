import { create } from 'zustand';
import type { Mode, Order } from '@/services/types';
import type { Services } from '@/services/registry';
import type { Scoped } from './types';

export interface OrdersState {
  orders: Order[];
  hydrate(svc: Services): Promise<void>;
  suspend(): void;
}

export function createOrdersStore<M extends Mode>(mode: M) {
  const store = create<OrdersState>((set) => ({
    orders: [],
    async hydrate(svc) {
      const ord = await svc.orders.list(mode);
      set({ orders: ord });
    },
    suspend() { set({ orders: [] }); },
  }));
  return store as unknown as Scoped<M, typeof store>;
}
