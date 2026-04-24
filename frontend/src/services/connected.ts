import type { ConnectedStatus } from './types';

export interface ConnectedService {
  snapshot(): ConnectedStatus[];
  subscribe(cb: (statuses: ConnectedStatus[]) => void): () => void;
}

const SEED: ConnectedStatus[] = [
  { assetClass: 'stock',   source: 'IBKR TWS',     state: 'live',    latencyMs: 120 },
  { assetClass: 'stock',   source: 'Schwab Stream',state: 'delayed', latencyMs: 15_000 },
  { assetClass: 'forex',   source: 'IBKR TWS',     state: 'live',    latencyMs: 80 },
  { assetClass: 'crypto',  source: 'Coinbase WS',  state: 'live',    latencyMs: 200 },
  { assetClass: 'futures', source: 'IBKR TWS',     state: 'down',    latencyMs: null },
];

export class MockConnectedService implements ConnectedService {
  private statuses: ConnectedStatus[] = SEED;
  private listeners = new Set<(s: ConnectedStatus[]) => void>();
  private timer: ReturnType<typeof setInterval> | null = null;

  snapshot() { return this.statuses; }

  subscribe(cb: (s: ConnectedStatus[]) => void): () => void {
    this.listeners.add(cb);
    if (!this.timer && this.listeners.size > 0) {
      this.timer = setInterval(() => this.mutate(), 4000);
    }
    return () => {
      this.listeners.delete(cb);
      if (this.listeners.size === 0 && this.timer) {
        clearInterval(this.timer);
        this.timer = null;
      }
    };
  }

  private mutate() {
    this.statuses = this.statuses.map(s =>
      s.latencyMs !== null
        ? { ...s, latencyMs: Math.max(50, s.latencyMs + (Math.random() - 0.5) * 50) }
        : s,
    );
    for (const cb of this.listeners) cb(this.statuses);
  }
}
