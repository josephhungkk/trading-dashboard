import type { AccountsService } from './accounts';
import type { PositionsService } from './positions';
import type { OrdersService } from './orders';
import type { QuotesService } from './quotes';
import type { WatchlistsService } from './watchlists';
import type { ConnectedService } from './connected';
import type { CommandRegistry } from './commands';
import { MockAccountsService } from './accounts';
import { MockPositionsService } from './positions';
import { MockOrdersService } from './orders';
import { MockQuotesService } from './quotes';
import { LocalStorageWatchlistService } from './watchlists';
import { MockConnectedService } from './connected';
import { InMemoryCommandRegistry } from './commands';

export interface Services {
  accounts: AccountsService;
  positions: PositionsService;
  orders: OrdersService;
  quotes: QuotesService;
  watchlists: WatchlistsService;
  connected: ConnectedService;
  commands: CommandRegistry;
}

class MemoryStorage implements Storage {
  private m = new Map<string, string>();
  get length() { return this.m.size; }
  clear() { this.m.clear(); }
  getItem(k: string) { return this.m.get(k) ?? null; }
  key(i: number) { return Array.from(this.m.keys())[i] ?? null; }
  removeItem(k: string) { this.m.delete(k); }
  setItem(k: string, v: string) { this.m.set(k, v); }
}

let _services: Services | null = null;

export function getServices(): Services {
  if (_services) return _services;
  _services = {
    accounts:   new MockAccountsService(),
    positions:  new MockPositionsService(),
    orders:     new MockOrdersService(),
    quotes:     new MockQuotesService(),
    watchlists: new LocalStorageWatchlistService(
      typeof window !== 'undefined' ? window.localStorage : new MemoryStorage()),
    connected:  new MockConnectedService(),
    commands:   new InMemoryCommandRegistry(),
  };
  return _services;
}

export function resetServices(): void { _services = null; }
