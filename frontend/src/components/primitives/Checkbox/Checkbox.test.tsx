import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Checkbox } from './Checkbox';

describe('Checkbox', () => {
  it('renders with checkbox role', () => {
    render(<Checkbox aria-label="accept" />);
    expect(screen.getByRole('checkbox')).toBeInTheDocument();
  });

  it('toggles when clicked (uncontrolled)', async () => {
    const user = userEvent.setup();
    render(<Checkbox aria-label="accept" />);
    const cb = screen.getByRole('checkbox');
    expect(cb.getAttribute('data-state')).toBe('unchecked');
    await user.click(cb);
    expect(cb.getAttribute('data-state')).toBe('checked');
  });

  it('does not toggle when disabled', async () => {
    const user = userEvent.setup();
    render(<Checkbox aria-label="accept" disabled />);
    const cb = screen.getByRole('checkbox');
    await user.click(cb);
    expect(cb.getAttribute('data-state')).toBe('unchecked');
  });
});
