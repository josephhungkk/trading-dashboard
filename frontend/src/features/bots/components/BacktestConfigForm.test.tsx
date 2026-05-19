import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { BacktestConfigForm } from './BacktestConfigForm';

vi.mock('../../../services/backtests/api', () => ({
  uploadBars: vi.fn(),
}));

describe('BacktestConfigForm', () => {
  it('submit button disabled when csv selected without upload', () => {
    render(<BacktestConfigForm botId="b1" onSubmit={vi.fn()} />);
    fireEvent.click(screen.getByDisplayValue('csv'));
    expect(screen.getByRole('button', { name: /run backtest/i })).toBeDisabled();
  });

  it('shows corporate action warning for long date ranges', () => {
    render(<BacktestConfigForm botId="b1" onSubmit={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/instrument/i), { target: { value: 'AAPL' } });
    fireEvent.change(screen.getByLabelText(/start date/i), { target: { value: '2022-01-01' } });
    fireEvent.change(screen.getByLabelText(/end date/i), { target: { value: '2024-01-01' } });
    expect(screen.getByRole('alert')).toHaveTextContent(/splits or dividends/i);
  });
});
