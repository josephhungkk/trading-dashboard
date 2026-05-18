import { render, fireEvent, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { FractionalQtyInput } from './FractionalQtyInput';

describe('FractionalQtyInput', () => {
  it('accepts valid decimal input within step precision', () => {
    const onChange = vi.fn();
    render(<FractionalQtyInput value='' onChange={onChange} step='0.01' decimals={2} />);
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '1.23' } });
    expect(onChange).toHaveBeenCalledWith('1.23');
  });

  it('shows error when precision exceeds decimals on blur', () => {
    render(<FractionalQtyInput value='1.234' onChange={vi.fn()} step='0.01' decimals={2} />);
    fireEvent.blur(screen.getByRole('spinbutton'));
    expect(screen.getByText(/precision/i)).toBeInTheDocument();
  });

  it('clears error when valid value entered after blur', () => {
    render(<FractionalQtyInput value='1.23' onChange={vi.fn()} step='0.01' decimals={2} />);
    fireEvent.blur(screen.getByRole('spinbutton'));
    expect(screen.queryByText(/precision/i)).not.toBeInTheDocument();
  });
});
