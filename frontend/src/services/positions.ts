import type { Position, Mode } from './types';
import { POSITIONS, ACCOUNTS } from './fixtures';

export interface PositionsService {
  list(mode: Mode): Promise<Position[]>;
  subscribe(mode: Mode, cb: (positions: Position[]) => void): () => void;
}

export class MockPositionsService implements PositionsService {
  constructor(private readonly fixtures: Position[] = POSITIONS) {}
  async list(mode: Mode): Promise<Position[]> {
    const ids = new Set(ACCOUNTS.filter(a => a.mode === mode).map(a => a.id));
    return this.fixtures.filter(p => ids.has(p.accountId));
  }
  subscribe(mode: Mode, cb: (positions: Position[]) => void): () => void {
    void mode;
    void cb;
    return () => {
      /* no-op until real adapter wires updates */
    };
  }
}
