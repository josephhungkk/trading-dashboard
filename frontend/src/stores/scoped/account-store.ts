import { create } from 'zustand';
import type { Mode, Account } from '@/services/types';
import type { Services } from '@/services/registry';
import type { Scoped } from './types';

export interface AccountsState {
  accounts: Account[];
  selectedAccountId: string | null;
  hydrate(svc: Services): Promise<void>;
  suspend(): void;
  select(id: string | null): void;
}

export function createAccountStore<M extends Mode>(mode: M) {
  const store = create<AccountsState>((set) => ({
    accounts: [],
    selectedAccountId: null,
    async hydrate(svc) {
      const accts = await svc.accounts.list(mode);
      set({ accounts: accts, selectedAccountId: accts[0]?.id ?? null });
    },
    suspend() { set({ accounts: [], selectedAccountId: null }); },
    select(id) { set({ selectedAccountId: id }); },
  }));
  return store as unknown as Scoped<M, typeof store>;
}
