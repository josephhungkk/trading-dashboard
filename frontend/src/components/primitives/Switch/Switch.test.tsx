import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Switch } from './Switch';

describe('Switch', () => {
  it('renders with switch role', () => {
    render(<Switch aria-label="notifications" />);
    expect(screen.getByRole('switch')).toBeInTheDocument();
  });

  it('toggles when clicked (uncontrolled)', async () => {
    const user = userEvent.setup();
    render(<Switch aria-label="t" />);
    const sw = screen.getByRole('switch');
    expect(sw.getAttribute('data-state')).toBe('unchecked');
    await user.click(sw);
    expect(sw.getAttribute('data-state')).toBe('checked');
  });

  it('does not toggle when disabled', async () => {
    const user = userEvent.setup();
    render(<Switch aria-label="t" disabled />);
    const sw = screen.getByRole('switch');
    await user.click(sw);
    expect(sw.getAttribute('data-state')).toBe('unchecked');
  });
});
