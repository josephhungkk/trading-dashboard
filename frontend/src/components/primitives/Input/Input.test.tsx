import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Input } from './Input';

describe('Input', () => {
  it('renders with placeholder', () => {
    render(<Input placeholder="email" />);
    expect(screen.getByPlaceholderText('email')).toBeInTheDocument();
  });

  it('applies numeric variant classes', () => {
    render(<Input variant="numeric" data-testid="num" defaultValue="1.23" />);
    const input = screen.getByTestId('num');
    expect(input.className).toContain('text-right');
    expect(input.className).toContain('font-mono');
    expect(input.className).toContain('tabular-nums');
  });

  it('accepts typed input when not disabled', async () => {
    const user = userEvent.setup();
    render(<Input placeholder="name" />);
    const input = screen.getByPlaceholderText('name') as HTMLInputElement;
    await user.type(input, 'hello');
    expect(input.value).toBe('hello');
  });

  it('does not accept input when disabled', async () => {
    const user = userEvent.setup();
    render(<Input placeholder="name" disabled defaultValue="" />);
    const input = screen.getByPlaceholderText('name') as HTMLInputElement;
    await user.type(input, 'blocked');
    expect(input.value).toBe('');
  });
});
