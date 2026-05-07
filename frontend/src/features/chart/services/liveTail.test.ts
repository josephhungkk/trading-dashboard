import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { openLiveTail } from './liveTail';
import type { BarEnvelope } from './liveTail';

// Minimal WebSocket mock with close-code support (needed for MED-1 test).
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  protocols: string | string[];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  sentMessages: string[] = [];
  closeCalled = false;

  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = protocols ?? [];
    MockWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sentMessages.push(data);
  }

  close(): void {
    this.closeCalled = true;
    this.onclose?.({ code: 1000 });
  }
}

describe('openLiveTail', () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('opens WS with bearer subprotocol using JWT from getJwt callback', () => {
    openLiveTail('AAPL.US', '1m', () => 'test-jwt', vi.fn());

    const ws = MockWebSocket.instances[0];
    expect(ws).toBeDefined();
    expect(ws?.protocols).toContain('bearer.test-jwt');
  });

  it('does not open WS when getJwt returns null', () => {
    openLiveTail('AAPL.US', '1m', () => null, vi.fn());
    expect(MockWebSocket.instances).toHaveLength(0);
  });

  it('sends subscribe message on open', () => {
    openLiveTail('AAPL.US', '1m', () => 'test-jwt', vi.fn());
    const ws = MockWebSocket.instances[0];
    ws?.onopen?.();

    const parsed = JSON.parse(ws?.sentMessages[0] ?? '{}') as Record<string, unknown>;
    expect(parsed['op']).toBe('subscribe');
    expect(parsed['canonical_id']).toBe('AAPL.US');
    expect(parsed['timeframe']).toBe('1m');
  });

  it('calls onMessage with parsed envelope', () => {
    const onMsg = vi.fn();
    openLiveTail('AAPL.US', '1m', () => 'test-jwt', onMsg);
    const ws = MockWebSocket.instances[0];
    ws?.onopen?.();

    const env: BarEnvelope = {
      canonical_id: 'AAPL.US',
      timeframe: '1m',
      bucket_start: '2026-05-07T14:00:00Z',
      open: '182.50',
      high: '183.10',
      low: '181.90',
      close: '182.75',
      volume: '42000',
      trade_count: 312,
      revision: 3,
      partial: true,
    };
    ws?.onmessage?.({ data: JSON.stringify(env) });

    expect(onMsg).toHaveBeenCalledWith(env);
  });

  it('responds to ping with pong', () => {
    openLiveTail('AAPL.US', '1m', () => 'test-jwt', vi.fn());
    const ws = MockWebSocket.instances[0];
    ws?.onopen?.();
    const initialSent = ws?.sentMessages.length ?? 0;

    ws?.onmessage?.({ data: JSON.stringify({ op: 'ping' }) });

    const pong = JSON.parse(ws?.sentMessages[initialSent] ?? '{}') as Record<string, unknown>;
    expect(pong['op']).toBe('pong');
  });

  it('reconnects after normal close with backoff', () => {
    openLiveTail('AAPL.US', '1m', () => 'test-jwt', vi.fn());
    const ws = MockWebSocket.instances[0];
    ws?.onopen?.();

    // Trigger close without calling handle.close() so reconnect fires.
    ws?.onclose?.({ code: 1000 });

    expect(MockWebSocket.instances).toHaveLength(1); // not yet

    vi.advanceTimersByTime(1000); // initial backoff = 1000ms

    expect(MockWebSocket.instances).toHaveLength(2); // reconnected
  });

  it('does not reconnect after handle.close()', () => {
    const handle = openLiveTail('AAPL.US', '1m', () => 'test-jwt', vi.fn());
    const ws = MockWebSocket.instances[0];
    ws?.onopen?.();

    handle.close();
    vi.advanceTimersByTime(5000);

    expect(MockWebSocket.instances).toHaveLength(1);
  });

  // MED-1: code 4001 = auth failure → no reconnect
  it('does not reconnect on close code 4001 (auth failure)', () => {
    openLiveTail('AAPL.US', '1m', () => 'test-jwt', vi.fn());
    const ws = MockWebSocket.instances[0];
    ws?.onopen?.();

    ws?.onclose?.({ code: 4001 });

    vi.advanceTimersByTime(10_000);

    // Must remain at exactly 1 — no reconnect attempt.
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  // HIGH-6: encodeURIComponent in WS URL
  it('percent-encodes canonicalId and timeframe in the WS URL', () => {
    openLiveTail('AAPL US/ADR', '1m', () => 'test-jwt', vi.fn());
    const ws = MockWebSocket.instances[0];
    expect(ws?.url).toContain('AAPL%20US%2FADR');
    expect(ws?.url).toContain('1m');
  });

  it('encodes special chars in timeframe', () => {
    openLiveTail('AAPL.US', '1d+extra', () => 'test-jwt', vi.fn());
    const ws = MockWebSocket.instances[0];
    expect(ws?.url).toContain('1d%2Bextra');
  });

  // HIGH-5: getJwt re-read on each reconnect
  it('re-reads JWT from getJwt callback on each reconnect', () => {
    let callCount = 0;
    const getJwt = vi.fn(() => {
      callCount++;
      return `jwt-${callCount}`;
    });

    openLiveTail('AAPL.US', '1m', getJwt, vi.fn());
    expect(getJwt).toHaveBeenCalledTimes(1);

    const ws1 = MockWebSocket.instances[0];
    ws1?.onopen?.();
    // Trigger reconnect
    ws1?.onclose?.({ code: 1000 });
    vi.advanceTimersByTime(1000);

    // getJwt called again for second connect
    expect(getJwt).toHaveBeenCalledTimes(2);
    expect(MockWebSocket.instances).toHaveLength(2);
    // Second WS uses updated JWT
    expect(MockWebSocket.instances[1]?.protocols).toContain('bearer.jwt-2');
  });

  it('aborts reconnect when getJwt returns null on reconnect', () => {
    let attempt = 0;
    const getJwt = vi.fn(() => {
      attempt++;
      return attempt === 1 ? 'initial-jwt' : null;
    });

    openLiveTail('AAPL.US', '1m', getJwt, vi.fn());
    const ws1 = MockWebSocket.instances[0];
    ws1?.onopen?.();

    // Trigger close → reconnect attempt → getJwt returns null → no new WS
    ws1?.onclose?.({ code: 1000 });
    vi.advanceTimersByTime(1000);

    // getJwt called for reconnect but returned null — no new WS created
    expect(getJwt).toHaveBeenCalledTimes(2);
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  // MED-4: onError callback surfaces error frames
  it('calls onError when an error frame is received', () => {
    const onError = vi.fn();
    openLiveTail('AAPL.US', '1m', () => 'test-jwt', vi.fn(), undefined, onError);
    const ws = MockWebSocket.instances[0];
    ws?.onopen?.();

    const errorFrame = { op: 'error', code: 'SYMBOL_NOT_FOUND', message: 'not found' };
    ws?.onmessage?.({ data: JSON.stringify(errorFrame) });

    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ op: 'error' }));
  });

  it('does not call onMessage for error frames', () => {
    const onMsg = vi.fn();
    openLiveTail('AAPL.US', '1m', () => 'test-jwt', onMsg);
    const ws = MockWebSocket.instances[0];
    ws?.onopen?.();

    ws?.onmessage?.({ data: JSON.stringify({ op: 'error', code: 'RATE_LIMIT' }) });

    expect(onMsg).not.toHaveBeenCalled();
  });
});
