import { create } from 'zustand';
import type { Mode, Account } from '@/services/types';
import type { Scoped } from './types';

export type FetchAccounts = (mode: Mode) => Promise<Account[]>;

export interface AccountsState {
  accounts: Account[];
  selectedAccountId: string | null;
  hydrate(fetchAccounts: FetchAccounts): Promise<void>;
  suspend(): void;
  select(id: string | null): void;
}

export function createAccountStore<M extends Mode>(mode: M) {
  const store = create<AccountsState>((set) => ({
    accounts: [],
    selectedAccountId: null,
    async hydrate(fetchAccounts) {
      const accts = await fetchAccounts(mode);
      set({ accounts: accts, selectedAccountId: accts[0]?.id ?? null });
    },
    suspend() { set({ accounts: [], selectedAccountId: null }); },
    select(id) { set({ selectedAccountId: id }); },
  }));
  return store as unknown as Scoped<M, typeof store>;
}
