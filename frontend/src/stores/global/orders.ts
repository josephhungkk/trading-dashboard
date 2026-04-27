import { create } from 'zustand';

export interface OrderResponse {
  id: string;
  last_event_at: string;
  [key: string]: unknown;
}

export interface OrderEventLike {
  id: string;
  last_event_at: string;
  [key: string]: unknown;
}

export interface BrokerMaintenance {
  active: boolean;
  window: 'weekend' | 'daily' | null;
  until: string | null;
}

interface OrdersState {
  orders: Record<string, OrderResponse>;
  killSwitchActive: boolean;
  brokerMaintenance: BrokerMaintenance | null;
  addOrder: (order: OrderResponse) => void;
  applyEvent: (event: OrderEventLike) => void;
  setKillSwitchActive: (active: boolean) => void;
  setBrokerMaintenance: (maintenance: BrokerMaintenance | null) => void;
  clear: () => void;
}

const initialState = {
  orders: {},
  killSwitchActive: false,
  brokerMaintenance: null,
} satisfies Pick<OrdersState, 'orders' | 'killSwitchActive' | 'brokerMaintenance'>;

export const useOrdersStore = create<OrdersState>((set, get) => ({
  ...initialState,
  addOrder: (order) => set((state) => ({
    orders: {
      ...state.orders,
      [order.id]: order,
    },
  })),
  applyEvent: (event) => {
    const existing = get().orders[event.id];
    if (existing && existing.last_event_at >= event.last_event_at) return;

    set((state) => ({
      orders: {
        ...state.orders,
        [event.id]: {
          ...existing,
          ...event,
        },
      },
    }));
  },
  setKillSwitchActive: (active) => set({ killSwitchActive: active }),
  setBrokerMaintenance: (maintenance) => set({ brokerMaintenance: maintenance }),
  clear: () => set(initialState),
}));
