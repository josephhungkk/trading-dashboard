import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AccountPicker } from './AccountPicker';
import { useModeStore } from '@/stores/global/mode';
import { useFleetMaintenance } from '@/stores/global/fleet-maintenance';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';
import { ACCOUNTS } from '@/services/fixtures';

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

describe('AccountPicker trade entry point', () => {
  beforeEach(async () => {
    stubRadixPointer();
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

  it('renders a trade button per account row', async () => {
    const user = userEvent.setup();
    render(<AccountPicker />);
    await user.click(screen.getByRole('button'));
    expect(await screen.findAllByRole('button', { name: 'Trade' })).toHaveLength(3);
  });

  it('disables a trade button when policy trading is disabled', async () => {
    const user = userEvent.setup();
    mockPolicy({ 'ibkr-paper-1': false });
    render(<AccountPicker />);
    await user.click(screen.getByRole('button'));
    const tradeButtons = await screen.findAllByRole('button', { name: 'Trade' });
    await waitFor(() => expect(tradeButtons[0]).toBeDisabled());
    expect(tradeButtons[0]).toHaveAttribute('title', 'Trading not enabled for this account');
  });
});
