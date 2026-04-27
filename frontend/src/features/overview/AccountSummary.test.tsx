import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AccountSummary } from './AccountSummary';
import { useModeStore } from '@/stores/global/mode';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';
import { fetchAccountsAndSyncMaintenance } from '@/hooks/useAccountsList';

describe('AccountSummary', () => {
  beforeEach(() => {
    resetServices();
    const { live, paper } = getBothScopes();
    live.suspend();
    paper.suspend();
    useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
  });

  it('renders the placeholder when no account is selected', () => {
    render(<AccountSummary />);
    expect(screen.getByText('Account Summary')).toBeInTheDocument();
    expect(screen.getByText('Select an account')).toBeInTheDocument();
  });

  it('renders alias and NLV when an account is selected', async () => {
    const { paper } = getBothScopes();
    await paper.hydrate(getServices(), fetchAccountsAndSyncMaintenance);
    render(<AccountSummary />);

    const selected = paper.useAccounts.getState();
    const account = selected.accounts.find((a) => a.id === selected.selectedAccountId);
    expect(account).toBeDefined();
    if (!account) return;

    // Alias and account number render verbatim in their <dd> cells.
    expect(screen.getByText(account.alias)).toBeInTheDocument();
    expect(screen.getByText(account.accountNumber)).toBeInTheDocument();
    // NLV is rendered via NumericCell currency formatting — the row must
    // contain at least one digit (i.e. not the em-dash placeholder).
    const nlvLabel = screen.getByText('NLV');
    const nlvRow = nlvLabel.parentElement;
    expect(nlvRow).not.toBeNull();
    expect(nlvRow?.textContent ?? '').toMatch(/\d/);
  });
});
