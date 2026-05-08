import type { Quote, Symbol } from './types';
import { SYMBOLS, STRESS_SYMBOLS } from './fixtures';
import { connectWs } from './ws';

export interface QuotesService {
  getSnapshot(symbol: string): Quote | undefined;
  subscribe(symbols: string[], cb: (q: Quote) => void): () => void;
  setFocus(canonicalId: string | null): void;
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

  setFocus(canonicalId: string | null): void {
    // Mock quotes have no upstream priority state.
    void canonicalId;
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

type QuoteCallback = (q: Quote) => void;

type ClientFrame =
  | { op: 'sub' | 'unsub'; symbols: string[] }
  | { op: 'focus'; canonical_id: string | null };

interface ServerFrame {
  op?: unknown;
  sym?: unknown;
  data?: unknown;
}

interface QuoteWireData {
  canonical_id?: unknown;
  tick_time?: unknown;
  received_at?: unknown;
  last?: unknown;
  bid?: unknown;
  ask?: unknown;
  volume?: unknown;
  day_high?: unknown;
  day_low?: unknown;
  open?: unknown;
  prev_close?: unknown;
  change_pct?: unknown;
  change?: unknown;
  is_delayed?: unknown;
}

const MAX_PENDING_FRAMES = 100;
const INITIAL_RECONNECT_BACKOFF_MS = 1000;
const MAX_RECONNECT_BACKOFF_MS = 30_000;
const MAX_RECONNECT_FAILURES = 3;

export class RealQuotesService implements QuotesService {
  private readonly subscriptions = new Map<string, Set<QuoteCallback>>();
  private readonly snapshots = new Map<string, Quote>();
  private focused: string | null = null;
  private reconnectBackoffMs = INITIAL_RECONNECT_BACKOFF_MS;
  private readonly pendingFrames: ClientFrame[] = [];
  private reconnectFailures = 0;
  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private fallback: MockQuotesService | null = null;
  private tickingEnabled = true;

  constructor(private readonly wsFactory: () => WebSocket = connectWs) {
    this.connect();
  }

  getSnapshot(symbol: string): Quote | undefined {
    return this.fallback?.getSnapshot(symbol) ?? this.snapshots.get(symbol);
  }

  subscribe(symbols: string[], cb: QuoteCallback): () => void {
    if (this.fallback) return this.fallback.subscribe(symbols, cb);

    const newlySubscribed: string[] = [];
    for (const sym of symbols) {
      let subs = this.subscriptions.get(sym);
      if (!subs) {
        subs = new Set<QuoteCallback>();
        this.subscriptions.set(sym, subs);
        newlySubscribed.push(sym);
      }
      subs.add(cb);
    }
    if (newlySubscribed.length > 0) this.send({ op: 'sub', symbols: newlySubscribed });

    return () => {
      if (this.fallback) return;

      const fullyUnsubscribed: string[] = [];
      for (const sym of symbols) {
        const subs = this.subscriptions.get(sym);
        if (!subs) continue;
        subs.delete(cb);
        if (subs.size === 0) {
          this.subscriptions.delete(sym);
          fullyUnsubscribed.push(sym);
        }
      }
      if (fullyUnsubscribed.length > 0) this.send({ op: 'unsub', symbols: fullyUnsubscribed });
    };
  }

  setFocus(canonicalId: string | null): void {
    if (this.fallback) {
      this.fallback.setFocus(canonicalId);
      return;
    }
    this.focused = canonicalId;
    this.send({ op: 'focus', canonical_id: canonicalId });
  }

  setTickingEnabled(on: boolean): void {
    this.tickingEnabled = on;
    this.fallback?.setTickingEnabled(on);
  }

  private connect(): void {
    if (this.fallback || !this.tickingEnabled) return;

    let ws: WebSocket;
    try {
      ws = this.wsFactory();
    } catch (error) {
      this.handleConnectionFailure(error);
      return;
    }

    this.ws = ws;
    ws.onopen = () => {
      this.reconnectFailures = 0;
      this.reconnectBackoffMs = INITIAL_RECONNECT_BACKOFF_MS;
      if (this.pendingFrames.length > 0) this.flushPendingFrames();
      else this.replayState();
    };
    ws.onmessage = (event: MessageEvent) => {
      this.handleMessage(event.data);
    };
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      this.handleConnectionFailure();
    };
    ws.onerror = () => {
      // The close event owns reconnect policy; browsers usually emit both.
    };
  }

  private replayState(): void {
    const symbols = Array.from(this.subscriptions.keys());
    if (symbols.length > 0) this.send({ op: 'sub', symbols });
    if (this.focused !== null) this.send({ op: 'focus', canonical_id: this.focused });
  }

  private handleConnectionFailure(error?: unknown): void {
    this.reconnectFailures += 1;
    if (this.reconnectFailures >= MAX_RECONNECT_FAILURES) {
      this.activateFallback(error);
      return;
    }
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer || this.fallback) return;
    const delayMs = this.reconnectBackoffMs;
    this.reconnectBackoffMs = Math.min(this.reconnectBackoffMs * 2, MAX_RECONNECT_BACKOFF_MS);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delayMs);
  }

  private activateFallback(error?: unknown): void {
    if (this.fallback) return;
    this.reconnectTimer = null;
    this.ws = null;
    this.fallback = new MockQuotesService();
    this.fallback.setTickingEnabled(this.tickingEnabled);
    console.warn('[quotes] falling back to mock quotes after websocket reconnect failures', error);
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new Event('quotes:fallback-banner'));
    }
  }

  private send(frame: ClientFrame): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(toArrayBuffer(encodeMsgpack(frame)));
      return;
    }
    this.enqueueFrame(frame);
  }

  private enqueueFrame(frame: ClientFrame): void {
    if (this.pendingFrames.length >= MAX_PENDING_FRAMES) this.pendingFrames.shift();
    this.pendingFrames.push(frame);
  }

  private flushPendingFrames(): void {
    while (this.pendingFrames.length > 0 && this.ws?.readyState === WebSocket.OPEN) {
      const frame = this.pendingFrames.shift();
      if (!frame) return;
      this.ws.send(toArrayBuffer(encodeMsgpack(frame)));
    }
  }

  private handleMessage(raw: unknown): void {
    let decoded: unknown;
    try {
      decoded = decodeMessage(raw);
    } catch (error) {
      console.warn('[quotes] failed to decode websocket frame', error);
      return;
    }

    if (!isRecord(decoded)) return;
    const frame = decoded as ServerFrame;
    if (frame.op === 'q' || frame.op === 'snap' || frame.op === 'stale') {
      this.handleQuoteFrame(frame, frame.op === 'stale');
    } else if (frame.op === 'err') {
      // MED fix: gate behind DEV to avoid console noise in production.
      if (import.meta.env.DEV) {
        console.warn('[quotes] websocket error frame', frame.data);
      }
    }
  }

  private handleQuoteFrame(frame: ServerFrame, isStale: boolean): void {
    // MED fix: server sends quote payload under "q" key (spec §7.2 alignment).
    // Fallback to "data" preserves compatibility if backend is on older rev.
    const payload = (frame as Record<string, unknown>)['q'] ?? frame.data;
    if (typeof frame.sym !== 'string' || !isRecord(payload)) return;
    const quote = quoteFromWire(frame.sym, payload as QuoteWireData, isStale);
    this.snapshots.set(frame.sym, quote);
    const subs = this.subscriptions.get(frame.sym);
    if (!subs) return;
    for (const cb of Array.from(subs)) {
      try {
        cb(quote);
      } catch (error) {
        console.warn('[quotes] subscriber callback failed', error);
      }
    }
  }
}

function decodeMessage(raw: unknown): unknown {
  if (raw instanceof ArrayBuffer) return decodeMsgpack(new Uint8Array(raw));
  if (ArrayBuffer.isView(raw)) {
    return decodeMsgpack(new Uint8Array(raw.buffer, raw.byteOffset, raw.byteLength));
  }
  throw new TypeError('unsupported websocket payload');
}

function quoteFromWire(sym: string, data: QuoteWireData, isStale: boolean): Quote {
  const last = toNumber(data.last);
  const bid = toNumber(data.bid);
  const ask = toNumber(data.ask);
  const prevClose = toNumber(data.prev_close);
  const change = data.change === undefined ? last - prevClose : toNumber(data.change);
  const spread = Math.max(0, ask - bid);
  const quote: Quote = {
    symbol: sym,
    last,
    change,
    changePct: toNumber(data.change_pct),
    bid,
    ask,
    volume: toNumber(data.volume),
    dayHigh: toNumber(data.day_high),
    dayLow: toNumber(data.day_low),
    open: toNumber(data.open),
    prevClose,
    fiftyTwoWkHigh: 0,
    fiftyTwoWkLow: 0,
    marketCap: null,
    pe: null,
    eps: null,
    divYield: null,
    beta: null,
    sector: null,
    industry: null,
    avgVol30d: 0,
    sharesOutstanding: null,
    nextEarningsDate: null,
    ivRank: null,
    optionsOI: null,
    newsCount24h: 0,
    spread,
    spreadPct: last > 0 ? spread / last : 0,
    isDelayed: data.is_delayed === true,
    asOf: timestampString(data.tick_time) ?? timestampString(data.received_at) ?? new Date().toISOString(),
  };
  return isStale ? Object.assign({}, quote, { isStale: true }) : quote;
}

function toNumber(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function timestampString(value: unknown): string | null {
  if (typeof value === 'string') return value;
  if (isRecord(value)) {
    const seconds = toNumber(value.seconds);
    const nanos = toNumber(value.nanos);
    if (seconds > 0) return new Date(seconds * 1000 + nanos / 1_000_000).toISOString();
  }
  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

function encodeMsgpack(value: unknown): Uint8Array {
  const chunks: number[] = [];
  writeMsgpack(value, chunks);
  return new Uint8Array(chunks);
}

function writeMsgpack(value: unknown, out: number[]): void {
  if (value === null) {
    out.push(0xc0);
    return;
  }
  if (typeof value === 'boolean') {
    out.push(value ? 0xc3 : 0xc2);
    return;
  }
  if (typeof value === 'number') {
    if (Number.isInteger(value) && value >= 0 && value <= 0x7f) {
      out.push(value);
      return;
    }
    const bytes = new Uint8Array(8);
    new DataView(bytes.buffer).setFloat64(0, value);
    out.push(0xcb, ...bytes);
    return;
  }
  if (typeof value === 'string') {
    writeMsgpackString(value, out);
    return;
  }
  if (Array.isArray(value)) {
    writeMsgpackArray(value, out);
    return;
  }
  if (isRecord(value)) {
    writeMsgpackMap(value, out);
    return;
  }
  throw new TypeError('unsupported msgpack value');
}

function writeMsgpackString(value: string, out: number[]): void {
  const bytes = new TextEncoder().encode(value);
  if (bytes.length < 32) {
    out.push(0xa0 | bytes.length, ...bytes);
    return;
  }
  if (bytes.length <= 0xff) {
    out.push(0xd9, bytes.length, ...bytes);
    return;
  }
  throw new RangeError('msgpack string too large');
}

function writeMsgpackArray(value: unknown[], out: number[]): void {
  if (value.length < 16) out.push(0x90 | value.length);
  else if (value.length <= 0xffff) out.push(0xdc, (value.length >> 8) & 0xff, value.length & 0xff);
  else throw new RangeError('msgpack array too large');
  for (const item of value) writeMsgpack(item, out);
}

function writeMsgpackMap(value: Record<string, unknown>, out: number[]): void {
  const entries = Object.entries(value);
  if (entries.length < 16) out.push(0x80 | entries.length);
  else if (entries.length <= 0xffff) out.push(0xde, (entries.length >> 8) & 0xff, entries.length & 0xff);
  else throw new RangeError('msgpack map too large');
  for (const [key, item] of entries) {
    writeMsgpackString(key, out);
    writeMsgpack(item, out);
  }
}

function decodeMsgpack(bytes: Uint8Array): unknown {
  const cursor = { offset: 0 };
  const value = readMsgpack(bytes, cursor);
  if (cursor.offset !== bytes.length) throw new TypeError('trailing msgpack bytes');
  return value;
}

function readMsgpack(bytes: Uint8Array, cursor: { offset: number }): unknown {
  const prefix = readByte(bytes, cursor);
  if (prefix <= 0x7f) return prefix;
  if (prefix >= 0x80 && prefix <= 0x8f) return readMsgpackMap(bytes, cursor, prefix & 0x0f);
  if (prefix >= 0x90 && prefix <= 0x9f) return readMsgpackArray(bytes, cursor, prefix & 0x0f);
  if (prefix >= 0xa0 && prefix <= 0xbf) return readMsgpackString(bytes, cursor, prefix & 0x1f);
  if (prefix === 0xc0) return null;
  if (prefix === 0xc2) return false;
  if (prefix === 0xc3) return true;
  if (prefix === 0xcb) return readFloat64(bytes, cursor);
  if (prefix === 0xcc) return readByte(bytes, cursor);
  if (prefix === 0xcd) return readUint16(bytes, cursor);
  if (prefix === 0xce) return readUint32(bytes, cursor);
  if (prefix === 0xd9) return readMsgpackString(bytes, cursor, readByte(bytes, cursor));
  if (prefix === 0xda) return readMsgpackString(bytes, cursor, readUint16(bytes, cursor));
  if (prefix === 0xdc) return readMsgpackArray(bytes, cursor, readUint16(bytes, cursor));
  if (prefix === 0xde) return readMsgpackMap(bytes, cursor, readUint16(bytes, cursor));
  throw new TypeError(`unsupported msgpack prefix ${prefix}`);
}

function readMsgpackMap(bytes: Uint8Array, cursor: { offset: number }, length: number): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (let i = 0; i < length; i += 1) {
    const key = readMsgpack(bytes, cursor);
    if (typeof key !== 'string') throw new TypeError('msgpack map key was not a string');
    result[key] = readMsgpack(bytes, cursor);
  }
  return result;
}

function readMsgpackArray(bytes: Uint8Array, cursor: { offset: number }, length: number): unknown[] {
  const result: unknown[] = [];
  for (let i = 0; i < length; i += 1) result.push(readMsgpack(bytes, cursor));
  return result;
}

function readMsgpackString(bytes: Uint8Array, cursor: { offset: number }, length: number): string {
  const start = cursor.offset;
  const end = start + length;
  if (end > bytes.length) throw new TypeError('truncated msgpack string');
  cursor.offset = end;
  return new TextDecoder().decode(bytes.subarray(start, end));
}

function readByte(bytes: Uint8Array, cursor: { offset: number }): number {
  const value = bytes[cursor.offset];
  if (value === undefined) throw new TypeError('truncated msgpack payload');
  cursor.offset += 1;
  return value;
}

function readUint16(bytes: Uint8Array, cursor: { offset: number }): number {
  const high = readByte(bytes, cursor);
  const low = readByte(bytes, cursor);
  return (high << 8) | low;
}

function readUint32(bytes: Uint8Array, cursor: { offset: number }): number {
  const b1 = readByte(bytes, cursor);
  const b2 = readByte(bytes, cursor);
  const b3 = readByte(bytes, cursor);
  const b4 = readByte(bytes, cursor);
  return ((b1 * 0x100 + b2) * 0x100 + b3) * 0x100 + b4;
}

function readFloat64(bytes: Uint8Array, cursor: { offset: number }): number {
  const start = cursor.offset;
  const end = start + 8;
  if (end > bytes.length) throw new TypeError('truncated msgpack float64');
  cursor.offset = end;
  return new DataView(bytes.buffer, bytes.byteOffset + start, 8).getFloat64(0);
}
