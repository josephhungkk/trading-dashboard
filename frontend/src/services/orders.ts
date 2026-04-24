import type { Order, Mode } from './types';
import { ORDERS, ACCOUNTS } from './fixtures';

export interface OrdersService {
  list(mode: Mode): Promise<Order[]>;
  subscribe(mode: Mode, cb: (orders: Order[]) => void): () => void;
}

export class MockOrdersService implements OrdersService {
  constructor(private readonly fixtures: Order[] = ORDERS) {}
  async list(mode: Mode): Promise<Order[]> {
    const ids = new Set(ACCOUNTS.filter(a => a.mode === mode).map(a => a.id));
    return this.fixtures.filter(o => ids.has(o.accountId));
  }
  subscribe(mode: Mode, cb: (orders: Order[]) => void): () => void {
    void mode;
    void cb;
    return () => {
      /* no-op until real adapter wires updates */
    };
  }
}
