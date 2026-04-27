import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { PositionsTable } from './PositionsTable';
import { useModeStore } from '@/stores/global/mode';
import { useFleetMaintenance } from '@/stores/global/fleet-maintenance';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';
import { ACCOUNTS } from '@/services/fixtures';

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

function mockPolicy(enabledByAccount: Record<string, boolean>): void {
  const fetchPolicy: typeof fetch = async (input) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const accountId = url.split('/').at(-1) ?? '';
    const tradeEnabled = enabledByAccount[decodeURIComponent(accountId)] ?? true;
    return new Response(JSON.stringify({ trade_enabled: tradeEnabled }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };
  vi.stubGlobal('fetch', fetchPolicy);
}

describe('PositionsTable trade entry point', () => {
  beforeEach(async () => {
    mockPolicy({});
    useFleetMaintenance.setState({
      maintenance: { active: false, window: null, until: null },
    });
    resetServices();
    const { live, paper } = getBothScopes();
    live.suspend();
    paper.suspend();
    useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
    await paper.hydrate(
      getServices(),
      async (mode) => ACCOUNTS.filter((account) => account.mode === mode),
    );
  });

  it('renders trade buttons for position rows', async () => {
    render(<PositionsTable />);
    expect(await screen.findAllByRole('button', { name: 'Trade' })).not.toHaveLength(0);
  });

  it('disables row trade buttons during maintenance', async () => {
    useFleetMaintenance.setState({
      maintenance: { active: true, window: 'daily', until: null },
    });
    render(<PositionsTable />);
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: 'Trade' })[0]).toHaveAttribute(
        'title',
        'Broker maintenance window — try again later',
      );
    });
    expect(screen.getAllByRole('button', { name: 'Trade' })[0]).toHaveAttribute(
      'title',
      'Broker maintenance window — try again later',
    );
  });
});
