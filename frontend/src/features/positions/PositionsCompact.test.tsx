import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PositionsCompact } from './PositionsCompact';
import { useModeStore } from '@/stores/global/mode';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';

class ResizeObserverStub {
  observe(): void { /* noop */ }
  unobserve(): void { /* noop */ }
  disconnect(): void { /* noop */ }
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
  configurable: true,
  get() { return 400; },
});
Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
  configurable: true,
  get() { return 800; },
});
Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
  configurable: true,
  get() { return 400; },
});
Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
  configurable: true,
  get() { return 800; },
});

function mkMql(matches: boolean, q: string): MediaQueryList {
  return {
    matches,
    media: q,
    onchange: null,
    addListener: () => { /* noop */ },
    removeListener: () => { /* noop */ },
    addEventListener: () => { /* noop */ },
    removeEventListener: () => { /* noop */ },
    dispatchEvent: () => false,
  } as unknown as MediaQueryList;
}
window.matchMedia = (q: string) => mkMql(q.includes('min-width'), q);

describe('PositionsCompact', () => {
  beforeEach(async () => {
    resetServices();
    const { live, paper } = getBothScopes();
    live.suspend();
    paper.suspend();
    useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
    await paper.hydrate(getServices());
  });

  it('renders heading and reduced column set (symbol, qty, pnl only)', async () => {
    render(<div style={{ height: 400 }}><PositionsCompact /></div>);
    expect(await screen.findByRole('heading', { name: 'Positions' })).toBeInTheDocument();
    expect(screen.getByText('Symbol')).toBeInTheDocument();
    expect(screen.getByText('Qty')).toBeInTheDocument();
    expect(screen.getByText('P&L')).toBeInTheDocument();
    // Full column set NOT present
    expect(screen.queryByText('Avg Cost')).not.toBeInTheDocument();
    expect(screen.queryByText('Market Value')).not.toBeInTheDocument();
    expect(screen.queryByText('Currency')).not.toBeInTheDocument();
  });

  it('filters to selected account — shows GOOGL (ibkr-paper-1) not KO (schwab-paper-1)', async () => {
    render(<div style={{ height: 400 }}><PositionsCompact /></div>);
    // The first paper account is auto-selected on hydrate (ibkr-paper-1).
    expect(await screen.findByText('GOOGL')).toBeInTheDocument();
    expect(screen.queryByText('KO')).not.toBeInTheDocument();
  });
});
