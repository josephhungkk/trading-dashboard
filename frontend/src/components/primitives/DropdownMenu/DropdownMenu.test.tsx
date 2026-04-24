import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from './DropdownMenu';

// Radix DropdownMenu relies on PointerEvents and hasPointerCapture which jsdom
// does not implement. Stub just enough to let userEvent.click drive it.
function stubRadixPointer(): void {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  if (typeof proto['hasPointerCapture'] !== 'function') {
    proto['hasPointerCapture'] = () => false;
  }
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

function renderBasic(props: {
  onProfileSelect?: () => void;
  onLogoutSelect?: () => void;
  logoutDisabled?: boolean;
} = {}) {
  const profileProps = props.onProfileSelect ? { onSelect: props.onProfileSelect } : {};
  const logoutProps = {
    ...(props.onLogoutSelect ? { onSelect: props.onLogoutSelect } : {}),
    ...(props.logoutDisabled ? { disabled: true } : {}),
  };
  return render(
    <DropdownMenu>
      <DropdownMenuTrigger>Open menu</DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuItem {...profileProps}>Profile</DropdownMenuItem>
        <DropdownMenuItem>Settings</DropdownMenuItem>
        <DropdownMenuItem {...logoutProps}>Logout</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>,
  );
}

describe('DropdownMenu', () => {
  it('renders the trigger but keeps the menu closed initially', () => {
    stubRadixPointer();
    renderBasic();
    expect(screen.getByRole('button', { name: 'Open menu' })).toBeInTheDocument();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('opens the menu and renders items when the trigger is clicked', async () => {
    stubRadixPointer();
    const user = userEvent.setup();
    renderBasic();
    await user.click(screen.getByRole('button', { name: 'Open menu' }));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Profile' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Settings' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Logout' })).toBeInTheDocument();
  });

  it('fires onSelect when an item is clicked', async () => {
    stubRadixPointer();
    const user = userEvent.setup();
    const onProfileSelect = vi.fn();
    renderBasic({ onProfileSelect });
    await user.click(screen.getByRole('button', { name: 'Open menu' }));
    await user.click(screen.getByRole('menuitem', { name: 'Profile' }));
    expect(onProfileSelect).toHaveBeenCalledTimes(1);
  });

  it('does not fire onSelect for a disabled item', async () => {
    stubRadixPointer();
    const user = userEvent.setup();
    const onLogoutSelect = vi.fn();
    renderBasic({ onLogoutSelect, logoutDisabled: true });
    await user.click(screen.getByRole('button', { name: 'Open menu' }));
    const logout = screen.getByRole('menuitem', { name: 'Logout' });
    expect(logout.getAttribute('data-disabled')).not.toBeNull();
    await user.click(logout);
    expect(onLogoutSelect).not.toHaveBeenCalled();
  });
});
