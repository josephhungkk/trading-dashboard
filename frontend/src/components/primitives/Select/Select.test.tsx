import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  Select,
  SelectTrigger,
  SelectContent,
  SelectItem,
  SelectValue,
} from './Select';

// Radix Select relies on PointerEvents and hasPointerCapture which jsdom
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

function renderBasic(
  props: { disabled?: boolean; onValueChange?: (v: string) => void } = {},
) {
  return render(
    <Select {...props}>
      <SelectTrigger aria-label="broker">
        <SelectValue placeholder="Pick" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="ibkr">Interactive Brokers</SelectItem>
        <SelectItem value="futu">Futu Securities</SelectItem>
        <SelectItem value="schwab">Charles Schwab</SelectItem>
      </SelectContent>
    </Select>,
  );
}

describe('Select', () => {
  it('renders a combobox trigger', () => {
    stubRadixPointer();
    renderBasic();
    expect(screen.getByRole('combobox', { name: 'broker' })).toBeInTheDocument();
  });

  it('opens the listbox when the trigger is clicked', async () => {
    stubRadixPointer();
    const user = userEvent.setup();
    renderBasic();
    const trigger = screen.getByRole('combobox', { name: 'broker' });
    expect(trigger.getAttribute('data-state')).toBe('closed');
    await user.click(trigger);
    expect(trigger.getAttribute('data-state')).toBe('open');
    expect(screen.getByRole('listbox')).toBeInTheDocument();
  });

  it('fires onValueChange with the selected value when an item is clicked', async () => {
    stubRadixPointer();
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    renderBasic({ onValueChange });
    await user.click(screen.getByRole('combobox', { name: 'broker' }));
    // Prefer clicking over keyboard nav — Radix keyboard handlers lean on
    // pointer/focus events that jsdom simulates imperfectly.
    await user.click(screen.getByRole('option', { name: 'Futu Securities' }));
    expect(onValueChange).toHaveBeenCalledWith('futu');
  });

  it('supports keyboard arrow navigation and selects via Enter', async () => {
    stubRadixPointer();
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    renderBasic({ onValueChange });
    const trigger = screen.getByRole('combobox', { name: 'broker' });
    trigger.focus();
    await user.keyboard('{Enter}');
    expect(trigger.getAttribute('data-state')).toBe('open');
    await user.keyboard('{ArrowDown}');
    await user.keyboard('{ArrowDown}');
    await user.keyboard('{Enter}');
    expect(onValueChange).toHaveBeenCalledTimes(1);
    const firstCall = onValueChange.mock.calls[0];
    const value = firstCall?.[0];
    expect(['ibkr', 'futu', 'schwab']).toContain(value);
  });

  it('does not open when disabled', async () => {
    stubRadixPointer();
    const user = userEvent.setup();
    renderBasic({ disabled: true });
    const trigger = screen.getByRole('combobox', { name: 'broker' });
    await user.click(trigger);
    expect(trigger.getAttribute('data-state')).toBe('closed');
  });
});
