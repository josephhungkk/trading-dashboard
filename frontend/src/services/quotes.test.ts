import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MockQuotesService, RealQuotesService } from './quotes';

type SentFrame = Record<string, unknown>;

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readonly protocols: string | string[] | undefined;
  binaryType: BinaryType = 'blob';
  readyState = MockWebSocket.CONNECTING;
  sent: Uint8Array[] = [];
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent<ArrayBuffer>) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = protocols;
    MockWebSocket.instances.push(this);
  }

  send(data: Parameters<WebSocket['send']>[0]): void {
    if (data instanceof Uint8Array) {
      this.sent.push(data);
      return;
    }
    if (data instanceof ArrayBuffer) {
      this.sent.push(new Uint8Array(data));
      return;
    }
    throw new TypeError('unexpected test websocket payload');
  }

  open(): void {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event('open'));
  }

  close(): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new Event('close') as CloseEvent);
  }

  message(frame: SentFrame): void {
    const bytes = encodeTestMsgpack(frame);
    const data = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
    this.onmessage?.(new MessageEvent('message', { data }));
  }
}

function installMockWebSocket(): void {
  MockWebSocket.instances = [];
  vi.stubGlobal('WebSocket', MockWebSocket);
}

function sentFrame(ws: MockWebSocket, index: number): SentFrame {
  const bytes = ws.sent[index];
  if (!bytes) throw new Error(`missing sent frame at index ${index}`);
  const decoded = decodeTestMsgpack(bytes);
  if (typeof decoded !== 'object' || decoded === null || Array.isArray(decoded)) {
    throw new Error('decoded sent frame was not an object');
  }
  return decoded as SentFrame;
}

function encodeTestMsgpack(value: unknown): Uint8Array {
  const chunks: number[] = [];
  writeTestMsgpack(value, chunks);
  return new Uint8Array(chunks);
}

function writeTestMsgpack(value: unknown, out: number[]): void {
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
    const bytes = new TextEncoder().encode(value);
    if (bytes.length >= 32) throw new RangeError('test string too large');
    out.push(0xa0 | bytes.length, ...bytes);
    return;
  }
  if (Array.isArray(value)) {
    if (value.length >= 16) throw new RangeError('test array too large');
    out.push(0x90 | value.length);
    for (const item of value) writeTestMsgpack(item, out);
    return;
  }
  if (typeof value === 'object' && value !== null) {
    const entries = Object.entries(value);
    if (entries.length >= 16) throw new RangeError('test map too large');
    out.push(0x80 | entries.length);
    for (const [key, item] of entries) {
      writeTestMsgpack(key, out);
      writeTestMsgpack(item, out);
    }
    return;
  }
  throw new TypeError('unsupported test msgpack value');
}

function decodeTestMsgpack(bytes: Uint8Array): unknown {
  const cursor = { offset: 0 };
  return readTestMsgpack(bytes, cursor);
}

function readTestMsgpack(bytes: Uint8Array, cursor: { offset: number }): unknown {
  const prefix = readTestByte(bytes, cursor);
  if (prefix <= 0x7f) return prefix;
  if (prefix >= 0x80 && prefix <= 0x8f) return readTestMap(bytes, cursor, prefix & 0x0f);
  if (prefix >= 0x90 && prefix <= 0x9f) return readTestArray(bytes, cursor, prefix & 0x0f);
  if (prefix >= 0xa0 && prefix <= 0xbf) return readTestString(bytes, cursor, prefix & 0x1f);
  if (prefix === 0xc0) return null;
  if (prefix === 0xc2) return false;
  if (prefix === 0xc3) return true;
  if (prefix === 0xcb) return readTestFloat64(bytes, cursor);
  throw new TypeError(`unsupported test msgpack prefix ${prefix}`);
}

function readTestMap(bytes: Uint8Array, cursor: { offset: number }, length: number): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (let i = 0; i < length; i += 1) {
    const key = readTestMsgpack(bytes, cursor);
    if (typeof key !== 'string') throw new TypeError('test map key was not string');
    result[key] = readTestMsgpack(bytes, cursor);
  }
  return result;
}

function readTestArray(bytes: Uint8Array, cursor: { offset: number }, length: number): unknown[] {
  const result: unknown[] = [];
  for (let i = 0; i < length; i += 1) result.push(readTestMsgpack(bytes, cursor));
  return result;
}

function readTestString(bytes: Uint8Array, cursor: { offset: number }, length: number): string {
  const start = cursor.offset;
  const end = start + length;
  cursor.offset = end;
  return new TextDecoder().decode(bytes.subarray(start, end));
}

function readTestByte(bytes: Uint8Array, cursor: { offset: number }): number {
  const value = bytes[cursor.offset];
  if (value === undefined) throw new TypeError('truncated test msgpack payload');
  cursor.offset += 1;
  return value;
}

function readTestFloat64(bytes: Uint8Array, cursor: { offset: number }): number {
  const start = cursor.offset;
  cursor.offset += 8;
  return new DataView(bytes.buffer, bytes.byteOffset + start, 8).getFloat64(0);
}

describe('MockQuotesService', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(()  => { vi.useRealTimers(); });

  it('getSnapshot returns seeded quote', () => {
    const svc = new MockQuotesService();
    expect(svc.getSnapshot('AAPL')).toBeDefined();
    expect(svc.getSnapshot('NONEXIST')).toBeUndefined();
  });

  it('timer does not start without subscribers', () => {
    const svc = new MockQuotesService();
    vi.advanceTimersByTime(2000);
    expect(svc.getSnapshot('AAPL')?.last).toBeGreaterThan(0);
  });

  it('subscribe starts timer and emits tick', () => {
    const svc = new MockQuotesService();
    const cb = vi.fn();
    svc.subscribe(['AAPL'], cb);
    vi.advanceTimersByTime(600);
    expect(cb).toHaveBeenCalled();
  });

  it('unsubscribe stops timer when refcount hits zero', () => {
    const svc = new MockQuotesService();
    const cb = vi.fn();
    const unsub = svc.subscribe(['AAPL'], cb);
    vi.advanceTimersByTime(600);
    unsub();
    cb.mockClear();
    vi.advanceTimersByTime(2000);
    expect(cb).not.toHaveBeenCalled();
  });

  it('setTickingEnabled(false) stops timer immediately', () => {
    const svc = new MockQuotesService();
    const cb = vi.fn();
    svc.subscribe(['AAPL'], cb);
    vi.advanceTimersByTime(600);
    cb.mockClear();
    svc.setTickingEnabled(false);
    vi.advanceTimersByTime(2000);
    expect(cb).not.toHaveBeenCalled();
  });
});

describe('RealQuotesService', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    installMockWebSocket();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('test_subscribes_a_symbol_and_forwards_ticks', () => {
    const svc = new RealQuotesService();
    const cb = vi.fn();
    svc.subscribe(['stock:AAPL:US'], cb);

    const ws = MockWebSocket.instances[0];
    expect(ws).toBeDefined();
    ws?.open();
    expect(sentFrame(ws as MockWebSocket, 0)).toMatchObject({ op: 'sub', symbols: ['stock:AAPL:US'] });

    ws?.message({
      op: 'q',
      sym: 'stock:AAPL:US',
      data: {
        last: '187.42',
        bid: '187.40',
        ask: '187.45',
        volume: '1000',
        prev_close: '185.00',
        change_pct: '0.0131',
      },
    });

    expect(cb).toHaveBeenCalledWith(expect.objectContaining({
      symbol: 'stock:AAPL:US',
      last: 187.42,
    }));
  });

  it('test_falls_back_to_mock_after_3_failed_reconnects', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const dispatch = vi.spyOn(window, 'dispatchEvent');
    const svc = new RealQuotesService();

    MockWebSocket.instances[0]?.close();
    vi.advanceTimersByTime(1000);
    MockWebSocket.instances[1]?.close();
    vi.advanceTimersByTime(2000);
    MockWebSocket.instances[2]?.close();

    expect(warn).toHaveBeenCalledWith(
      '[quotes] falling back to mock quotes after websocket reconnect failures',
      undefined,
    );
    expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({ type: 'quotes:fallback-banner' }));

    const cb = vi.fn();
    svc.subscribe(['AAPL'], cb);
    vi.advanceTimersByTime(600);
    expect(cb).toHaveBeenCalled();
  });

  it('test_unsubscribe_sends_unsub_only_on_last_callback', () => {
    const svc = new RealQuotesService();
    const cb1 = vi.fn();
    const cb2 = vi.fn();
    const unsub1 = svc.subscribe(['stock:AAPL:US'], cb1);
    const unsub2 = svc.subscribe(['stock:AAPL:US'], cb2);
    const ws = MockWebSocket.instances[0];
    expect(ws).toBeDefined();
    ws?.open();
    expect(ws?.sent).toHaveLength(1);

    unsub1();
    expect(ws?.sent).toHaveLength(1);

    unsub2();
    expect(ws?.sent).toHaveLength(2);
    expect(sentFrame(ws as MockWebSocket, 1)).toMatchObject({ op: 'unsub', symbols: ['stock:AAPL:US'] });
  });

  it('test_setFocus_sends_focus_frame', () => {
    const svc = new RealQuotesService();
    const ws = MockWebSocket.instances[0];
    expect(ws).toBeDefined();
    ws?.open();

    svc.setFocus('stock:AAPL:US');

    expect(sentFrame(ws as MockWebSocket, 0)).toMatchObject({
      op: 'focus',
      canonical_id: 'stock:AAPL:US',
    });
  });

  it('test_pending_frames_dropped_at_100_oldest_first', () => {
    const svc = new RealQuotesService();
    for (let i = 0; i < 110; i += 1) {
      svc.subscribe([`stock:SYM${i}:US`], vi.fn());
    }

    const pending = Reflect.get(svc, 'pendingFrames') as SentFrame[];
    expect(pending).toHaveLength(100);
    expect(pending[0]).toMatchObject({ op: 'sub', symbols: ['stock:SYM10:US'] });
    expect(pending[99]).toMatchObject({ op: 'sub', symbols: ['stock:SYM109:US'] });
  });

  it('test_stale_op_marks_quote_stale_and_forwards', () => {
    const svc = new RealQuotesService();
    const cb = vi.fn();
    svc.subscribe(['stock:AAPL:US'], cb);
    const ws = MockWebSocket.instances[0];
    expect(ws).toBeDefined();
    ws?.open();

    ws?.message({
      op: 'stale',
      sym: 'stock:AAPL:US',
      data: {
        last: '187.42',
        bid: '187.40',
        ask: '187.45',
        volume: '1000',
        prev_close: '185.00',
        change_pct: '0.0131',
      },
    });

    expect(cb).toHaveBeenCalledWith(expect.objectContaining({
      symbol: 'stock:AAPL:US',
      isStale: true,
    }));
  });
});
