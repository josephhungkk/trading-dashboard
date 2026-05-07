import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { openLiveTail } from './liveTail';
import type { BarEnvelope } from './liveTail';

// Minimal WebSocket mock
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  protocols: string | string[];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
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
    this.onclose?.();
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

  it('opens WS with bearer subprotocol', () => {
    openLiveTail('AAPL.US', '1m', 'test-jwt', vi.fn());

    const ws = MockWebSocket.instances[0];
    expect(ws).toBeDefined();
    expect(ws?.protocols).toContain('bearer.test-jwt');
  });

  it('sends subscribe message on open', () => {
    openLiveTail('AAPL.US', '1m', 'test-jwt', vi.fn());
    const ws = MockWebSocket.instances[0];
    ws?.onopen?.();

    const parsed = JSON.parse(ws?.sentMessages[0] ?? '{}') as Record<string, unknown>;
    expect(parsed['op']).toBe('subscribe');
    expect(parsed['canonical_id']).toBe('AAPL.US');
    expect(parsed['timeframe']).toBe('1m');
  });

  it('calls onMessage with parsed envelope', () => {
    const onMsg = vi.fn();
    openLiveTail('AAPL.US', '1m', 'test-jwt', onMsg);
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
    openLiveTail('AAPL.US', '1m', 'test-jwt', vi.fn());
    const ws = MockWebSocket.instances[0];
    ws?.onopen?.();
    const initialSent = ws?.sentMessages.length ?? 0;

    ws?.onmessage?.({ data: JSON.stringify({ op: 'ping' }) });

    const pong = JSON.parse(ws?.sentMessages[initialSent] ?? '{}') as Record<string, unknown>;
    expect(pong['op']).toBe('pong');
  });

  it('reconnects after close with backoff', () => {
    openLiveTail('AAPL.US', '1m', 'test-jwt', vi.fn());
    const ws = MockWebSocket.instances[0];
    ws?.onopen?.();

    // Trigger close without calling handle.close() so reconnect fires.
    // closeCalled must be false so the onclose handler schedules reconnect.
    if (ws) ws.closeCalled = false;
    ws?.onclose?.();

    expect(MockWebSocket.instances).toHaveLength(1); // not yet

    vi.advanceTimersByTime(1000); // initial backoff = 1000ms

    expect(MockWebSocket.instances).toHaveLength(2); // reconnected
  });

  it('does not reconnect after handle.close()', () => {
    const handle = openLiveTail('AAPL.US', '1m', 'test-jwt', vi.fn());
    const ws = MockWebSocket.instances[0];
    ws?.onopen?.();

    handle.close();
    vi.advanceTimersByTime(5000);

    expect(MockWebSocket.instances).toHaveLength(1);
  });
});
