import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { OpenOrdersCompact } from './OpenOrdersCompact';
import { useModeStore } from '@/stores/global/mode';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';
import { fetchAccountsAndSyncMaintenance } from '@/hooks/useAccountsList';

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

describe('OpenOrdersCompact', () => {
  beforeEach(async () => {
    resetServices();
    const { live, paper } = getBothScopes();
    live.suspend();
    paper.suspend();
    useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
    await paper.hydrate(getServices(), fetchAccountsAndSyncMaintenance);
  });

  it('renders heading and reduced column set', async () => {
    render(<div style={{ height: 400 }}><OpenOrdersCompact /></div>);
    expect(await screen.findByRole('heading', { name: 'Open Orders' })).toBeInTheDocument();
    expect(screen.getByText('Symbol')).toBeInTheDocument();
    expect(screen.getByText('Side')).toBeInTheDocument();
    expect(screen.getByText('Status')).toBeInTheDocument();
    // Compact column set does NOT include Type/Created
    expect(screen.queryByText('Type')).not.toBeInTheDocument();
    expect(screen.queryByText('Created')).not.toBeInTheDocument();
  });

  it('includes paper-mode open order BTC-USD (ord-004)', async () => {
    render(<div style={{ height: 400 }}><OpenOrdersCompact /></div>);
    expect(await screen.findByText('BTC-USD')).toBeInTheDocument();
  });

  it('excludes cancelled and rejected orders (GOOGL ord-014 cancelled, AMZN ord-018 rejected)', async () => {
    render(<div style={{ height: 400 }}><OpenOrdersCompact /></div>);
    await screen.findByText('BTC-USD');
    expect(screen.queryByText('AMZN')).not.toBeInTheDocument();
    expect(screen.queryByText('GOOGL')).not.toBeInTheDocument();
  });
});
