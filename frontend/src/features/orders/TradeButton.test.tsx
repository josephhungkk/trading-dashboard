import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AccountPicker } from '@/features/accounts/AccountPicker';
import { PositionsTable } from '@/features/positions/PositionsTable';
import { useModeStore } from '@/stores/global/mode';
import { useFleetMaintenance } from '@/stores/global/fleet-maintenance';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';
import { ACCOUNTS } from '@/services/fixtures';
import { renderWithQuery } from '@/test-utils/render-with-query';
import { tradeTicketStore } from './use-trade-ticket';
import type { Position } from '@/services/types';

interface PositionWithConid extends Position {
  conid: string;
}

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

function stubRadixPointer(): void {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  if (typeof proto['hasPointerCapture'] !== 'function') proto['hasPointerCapture'] = () => false;
  if (typeof proto['releasePointerCapture'] !== 'function') {
    proto['releasePointerCapture'] = () => { /* jsdom stub */ };
  }
  if (typeof proto['setPointerCapture'] !== 'function') {
    proto['setPointerCapture'] = () => { /* jsdom stub */ };
  }
  if (typeof proto['scrollIntoView'] !== 'function') {
    proto['scrollIntoView'] = () => { /* jsdom stub */ };
  }
}

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

async function hydratePaper(): Promise<void> {
  resetServices();
  const { live, paper } = getBothScopes();
  live.suspend();
  paper.suspend();
  useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
  await paper.hydrate(
    getServices(),
    async (mode) => ACCOUNTS.filter((account) => account.mode === mode),
  );
}

describe('TradeButton', () => {
  beforeEach(async () => {
    stubRadixPointer();
    mockPolicy({});
    useFleetMaintenance.setState({
      maintenance: { active: false, window: null, until: null },
    });
    tradeTicketStore.setState({
      isOpen: false,
      accountId: null,
      defaultConid: null,
      defaultSymbol: null,
      clientOrderId: null,
      preview: null,
      inFlight: false,
    });
    await hydratePaper();
  });

  it('account_picker_trade_button_opens_modal_with_account_id', async () => {
    const user = userEvent.setup();
    renderWithQuery(<AccountPicker />);
    await user.click(screen.getByRole('button'));
    const tradeButtons = await screen.findAllByRole('button', { name: 'Trade' });
    await waitFor(() => expect(tradeButtons[0]).toBeEnabled());
    const firstTradeButton = tradeButtons[0];
    if (firstTradeButton === undefined) throw new Error('Trade button not found');
    await user.click(firstTradeButton);
    expect(screen.getByTestId('trade-ticket-account-id')).toHaveTextContent('ibkr-paper-1');
    expect(tradeTicketStore.getState().accountId).toBe('ibkr-paper-1');
  });

  it('account_picker_trade_button_disabled_when_trade_enabled_false', async () => {
    const user = userEvent.setup();
    mockPolicy({ 'ibkr-paper-1': false });
    render(<AccountPicker />);
    await user.click(screen.getByRole('button'));
    const tradeButtons = await screen.findAllByRole('button', { name: 'Trade' });
    await waitFor(() => expect(tradeButtons[0]).toBeDisabled());
    expect(tradeButtons[0]).toHaveAttribute('title', 'Trading not enabled for this account');
  });

  it('positions_row_trade_button_pre_populates_conid_and_symbol', async () => {
    const user = userEvent.setup();
    const { paper } = getBothScopes();
    const position: PositionWithConid = {
      accountId: 'ibkr-paper-1',
      symbol: 'AAPL',
      conid: '265598',
      qty: 1,
      avgCost: 100,
      marketValue: 110,
      pnlUnrealized: 10,
      pnlRealized: 0,
      currency: 'USD',
      asOf: '2026-04-24T10:00:00Z',
    };
    paper.usePositions.setState({ positions: [position] });
    renderWithQuery(<PositionsTable />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Trade' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: 'Trade' }));
    expect(screen.getByRole('dialog', { name: 'Trade ticket' })).toBeInTheDocument();
    expect(tradeTicketStore.getState().defaultConid).toBe('265598');
    expect(tradeTicketStore.getState().defaultSymbol).toBe('AAPL');
  });

  it('trade_button_disabled_during_maintenance', async () => {
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
