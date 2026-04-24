import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AccountPicker } from './AccountPicker';
import { useModeStore } from '@/stores/global/mode';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';

function stubRadixPointer(): void {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  if (typeof proto['hasPointerCapture'] !== 'function') proto['hasPointerCapture'] = () => false;
  if (typeof proto['releasePointerCapture'] !== 'function')
    proto['releasePointerCapture'] = () => {
      /* jsdom stub */
    };
  if (typeof proto['setPointerCapture'] !== 'function')
    proto['setPointerCapture'] = () => {
      /* jsdom stub */
    };
  if (typeof proto['scrollIntoView'] !== 'function')
    proto['scrollIntoView'] = () => {
      /* jsdom stub */
    };
}

describe('AccountPicker', () => {
  beforeEach(async () => {
    stubRadixPointer();
    resetServices();
    const { live, paper } = getBothScopes();
    live.suspend();
    paper.suspend();
    useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
    await paper.hydrate(getServices());
  });

  it('renders selected account alias in trigger when hydrated', () => {
    render(<AccountPicker />);
    const trigger = screen.getByRole('button');
    expect(trigger).toBeInTheDocument();
    expect(trigger.textContent).not.toContain('Select account');
  });

  it('shows "Select account" when no accounts loaded', () => {
    const { paper } = getBothScopes();
    paper.suspend();
    render(<AccountPicker />);
    expect(screen.getByText('Select account')).toBeInTheDocument();
  });

  it('opens dropdown menu when trigger clicked', async () => {
    const user = userEvent.setup();
    render(<AccountPicker />);
    await user.click(screen.getByRole('button'));
    const menuItems = screen.getAllByRole('menuitem');
    expect(menuItems.length).toBeGreaterThan(0);
  });

  it('clicking a menu item updates the selected account', async () => {
    const user = userEvent.setup();
    const { paper } = getBothScopes();
    render(<AccountPicker />);
    await user.click(screen.getByRole('button'));
    const items = screen.getAllByRole('menuitem');
    const initialSelectedId = paper.useAccounts.getState().selectedAccountId;
    if (items.length > 1) {
      const target = items[1];
      if (target) {
        await user.click(target);
        const newSelectedId = paper.useAccounts.getState().selectedAccountId;
        expect(newSelectedId).not.toBe(initialSelectedId);
      }
    } else {
      const first = items[0];
      if (first) {
        await user.click(first);
        const id = paper.useAccounts.getState().selectedAccountId;
        expect(id).toBeTruthy();
      }
    }
  });
});
