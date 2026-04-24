import type { Watchlist } from './types';
import { WATCHLISTS } from './fixtures';

const STORAGE_KEY = 'dashboard.watchlists.v1';

export interface WatchlistsService {
  list(): Promise<Watchlist[]>;
  save(watchlists: Watchlist[]): Promise<void>;
}

export class LocalStorageWatchlistService implements WatchlistsService {
  constructor(private readonly storage: Storage) {}
  async list(): Promise<Watchlist[]> {
    const raw = this.storage.getItem(STORAGE_KEY);
    if (!raw) return [...WATCHLISTS];
    try { return JSON.parse(raw) as Watchlist[]; }
    catch { return [...WATCHLISTS]; }
  }
  async save(watchlists: Watchlist[]): Promise<void> {
    this.storage.setItem(STORAGE_KEY, JSON.stringify(watchlists));
  }
}
