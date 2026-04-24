import type { Quote, Symbol } from './types';
import { SYMBOLS, STRESS_SYMBOLS } from './fixtures';

export interface QuotesService {
  getSnapshot(symbol: string): Quote | undefined;
  subscribe(symbols: string[], cb: (q: Quote) => void): () => void;
  setTickingEnabled(on: boolean): void;
}

function seedQuote(sym: Symbol): Quote {
  const base = 50 + (sym.symbol.charCodeAt(0) % 200);
  const spread = base * 0.0005;
  return {
    symbol: sym.symbol,
    last: base, change: 0, changePct: 0,
    bid: base - spread / 2, ask: base + spread / 2,
    volume: 1_000_000 + (sym.symbol.charCodeAt(1) ?? 0) * 10_000,
    dayHigh: base * 1.02, dayLow: base * 0.98,
    open: base * 0.99, prevClose: base,
    fiftyTwoWkHigh: base * 1.5, fiftyTwoWkLow: base * 0.5,
    marketCap: base * 1_000_000_000,
    pe: 20 + sym.symbol.length * 2,
    eps: 2.5, divYield: 0.015, beta: 1.0 + (sym.symbol.charCodeAt(0) % 5) * 0.1,
    sector: sym.assetClass === 'stock' ? 'Technology' : null,
    industry: null,
    avgVol30d: 900_000,
    sharesOutstanding: sym.assetClass === 'stock' ? 1_000_000_000 : null,
    nextEarningsDate: '2026-05-15',
    ivRank: 50, optionsOI: 10_000, newsCount24h: 3,
    spread, spreadPct: spread / base,
    isDelayed: sym.exchange === 'SEHK' || sym.exchange === 'TSE',
    asOf: new Date().toISOString(),
  };
}

export class MockQuotesService implements QuotesService {
  private readonly quotes = new Map<string, Quote>();
  private readonly subscriptions = new Map<string, Set<(q: Quote) => void>>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private tickingEnabled = true;

  constructor(syms: Symbol[] = [...SYMBOLS, ...STRESS_SYMBOLS]) {
    for (const s of syms) this.quotes.set(s.symbol, seedQuote(s));
  }

  getSnapshot(symbol: string): Quote | undefined {
    return this.quotes.get(symbol);
  }

  subscribe(symbols: string[], cb: (q: Quote) => void): () => void {
    for (const sym of symbols) {
      if (!this.subscriptions.has(sym)) this.subscriptions.set(sym, new Set());
      const subs = this.subscriptions.get(sym);
      if (subs) subs.add(cb);
    }
    this.maybeStartTimer();
    return () => {
      for (const sym of symbols) {
        const subs = this.subscriptions.get(sym);
        if (subs) {
          subs.delete(cb);
          if (subs.size === 0) this.subscriptions.delete(sym);
        }
      }
      this.maybeStopTimer();
    };
  }

  setTickingEnabled(on: boolean): void {
    this.tickingEnabled = on;
    if (!on && this.timer) { clearInterval(this.timer); this.timer = null; }
    else if (on) { this.maybeStartTimer(); }
  }

  private maybeStartTimer(): void {
    if (this.timer || !this.tickingEnabled || this.subscriptions.size === 0) return;
    this.timer = setInterval(() => { this.tick(); }, 500);
  }

  private maybeStopTimer(): void {
    if (this.timer && this.subscriptions.size === 0) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private tick(): void {
    for (const [sym, subs] of this.subscriptions) {
      const prev = this.quotes.get(sym);
      if (!prev) continue;
      const delta = (Math.random() - 0.5) * prev.last * 0.002;
      const last = Math.max(0.01, prev.last + delta);
      const next: Quote = {
        ...prev,
        last,
        change: last - prev.prevClose,
        changePct: (last - prev.prevClose) / prev.prevClose,
        asOf: new Date().toISOString(),
      };
      this.quotes.set(sym, next);
      for (const cb of subs) cb(next);
    }
  }
}
