import type { AccountsService } from './accounts';
import type { PositionsService } from './positions';
import type { OrdersService } from './orders';
import type { QuotesService } from './quotes';
import type { WatchlistsService } from './watchlists';
import type { ConnectedService } from './connected';
import type { QuoteFeedService } from './quote-feeds';
import type { CommandRegistry } from './commands';
import { MockAccountsService, RealAccountsService } from './accounts';
import { MockPositionsService } from './positions';
import { MockOrdersService } from './orders';
import { MockQuotesService, RealQuotesService } from './quotes';
import { LocalStorageWatchlistService } from './watchlists';
import { MockConnectedService } from './connected';
import { MockQuoteFeedService } from './quote-feeds';
import { InMemoryCommandRegistry } from './commands';

export interface Services {
  accounts: AccountsService;
  positions: PositionsService;
  orders: OrdersService;
  quotes: QuotesService;
  watchlists: WatchlistsService;
  connected: ConnectedService;
  quoteFeeds: QuoteFeedService;
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

const USE_MOCKS = (import.meta.env.VITE_USE_MOCKS as string | undefined) === 'true';
const IS_TEST = import.meta.env.MODE === 'test';
const USE_MOCK_QUOTES =
  IS_TEST || USE_MOCKS || (import.meta.env.VITE_QUOTES_USE_MOCK as string | undefined) === 'true';

export function getServices(): Services {
  if (_services) return _services;
  _services = {
    // RealAccountsService fetches from /api/accounts and maps the wire
    // shape (M22 boundary-stripped) onto the display Account shape.
    // Storybook + Vitest pin VITE_USE_MOCKS=true so they keep the
    // synthetic ACCOUNTS fixtures.
    accounts:   USE_MOCKS ? new MockAccountsService() : new RealAccountsService(),
    positions:  new MockPositionsService(),
    orders:     new MockOrdersService(),
    quotes:     USE_MOCK_QUOTES ? new MockQuotesService() : new RealQuotesService(),
    watchlists: new LocalStorageWatchlistService(
      typeof window !== 'undefined' ? window.localStorage : new MemoryStorage()),
    connected:  new MockConnectedService(),
    quoteFeeds: new MockQuoteFeedService(),
    commands:   new InMemoryCommandRegistry(),
  };
  return _services;
}

export function resetServices(): void { _services = null; }
