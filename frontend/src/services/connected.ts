import type { ConnectedStatus } from './types';

export interface ConnectedService {
  snapshot(): ConnectedStatus[];
  subscribe(cb: (statuses: ConnectedStatus[]) => void): () => void;
}

const SEED: ConnectedStatus[] = [
  { broker: 'ibkr',   mode: 'live',  gatewayId: 'ibkr-live-gw-1',  alias: 'IBKR Live Gateway 1',  backendOk: true,  gatewayOk: true,  latencyMs: 120 },
  { broker: 'ibkr',   mode: 'live',  gatewayId: 'ibkr-live-gw-2',  alias: 'IBKR Live Gateway 2',  backendOk: true,  gatewayOk: false, latencyMs: 240 },
  { broker: 'ibkr',   mode: 'paper', gatewayId: 'ibkr-paper-gw-1', alias: 'IBKR Paper Gateway 1', backendOk: true,  gatewayOk: true,  latencyMs: 140 },
  { broker: 'ibkr',   mode: 'paper', gatewayId: 'ibkr-paper-gw-2', alias: 'IBKR Paper Gateway 2', backendOk: true,  gatewayOk: true,  latencyMs: 160 },
  { broker: 'futu',   gatewayId: 'futu-od-1',    alias: 'Futu OpenD',  backendOk: true,  gatewayOk: true,  latencyMs: 80 },
  { broker: 'schwab', gatewayId: 'schwab-api-1', alias: 'Schwab API',  backendOk: false, gatewayOk: false, latencyMs: null },
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
