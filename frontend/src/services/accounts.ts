import type { Account, Mode } from './types';
import { ACCOUNTS } from './fixtures';

export interface AccountsService {
  list(mode: Mode): Promise<Account[]>;
  subscribe(mode: Mode, cb: (accounts: Account[]) => void): () => void;
}

export class MockAccountsService implements AccountsService {
  constructor(private readonly fixtures: Account[] = ACCOUNTS) {}
  async list(mode: Mode): Promise<Account[]> {
    return this.fixtures.filter(a => a.mode === mode);
  }
  subscribe(mode: Mode, cb: (accounts: Account[]) => void): () => void {
    void mode;
    void cb;
    return () => {
      /* no-op until real adapter wires updates */
    };
  }
}
