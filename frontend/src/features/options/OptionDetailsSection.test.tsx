import { expect, test, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { OptionDetailsSection } from './OptionDetailsSection';
import type { OptionChainRow } from './types';

const mockRow: OptionChainRow = {
  conid: '12345678',
  strike: '450.00',
  put_call: 'C',
  bid: '5.10',
  ask: '5.30',
  iv: 0.175,
  delta: 0.5,
  gamma: 0.028,
  theta: -0.12,
  vega: 0.31,
  open_interest: 38000,
  volume: 1200,
  multiplier: 100,
  exchange: 'CBOE',
  style: 'A',
};

test('renders contract label correctly', () => {
  render(
    <OptionDetailsSection
      row={mockRow}
      underlyingSymbol="SPY"
      expiryIso="2025-01-17"
      onSideChange={vi.fn()}
    />,
  );
  expect(screen.getAllByText(/SPY/).length).toBeGreaterThan(0);
  expect(screen.getByText(/450\.00C/)).toBeInTheDocument();
});

test('greeks strip renders delta', () => {
  render(
    <OptionDetailsSection
      row={mockRow}
      underlyingSymbol="SPY"
      expiryIso="2025-01-17"
      onSideChange={vi.fn()}
    />,
  );
  const strip = screen.getByTestId('greeks-strip');
  expect(strip).toHaveTextContent('0.500');
});

test('notional is multiplier × premium', () => {
  render(
    <OptionDetailsSection
      row={mockRow}
      underlyingSymbol="SPY"
      expiryIso="2025-01-17"
      onSideChange={vi.fn()}
    />,
  );
  // premium = (5.10 + 5.30) / 2 = 5.20; notional = 5.20 * 100 = 520.00
  expect(screen.getByText(/520\.00/)).toBeInTheDocument();
});

test('STO button calls onSideChange with SELL OPEN', () => {
  const handler = vi.fn();
  render(
    <OptionDetailsSection
      row={mockRow}
      underlyingSymbol="SPY"
      expiryIso="2025-01-17"
      onSideChange={handler}
    />,
  );
  fireEvent.click(screen.getByTestId('leg-select-STO'));
  expect(handler).toHaveBeenCalledWith('SELL', 'OPEN');
});

test('zero_dte banner shows when expiry is today', () => {
  const today = new Date().toISOString().slice(0, 10);
  render(
    <OptionDetailsSection
      row={mockRow}
      underlyingSymbol="SPY"
      expiryIso={today}
      onSideChange={vi.fn()}
    />,
  );
  expect(screen.getByTestId('zero-dte-banner')).toBeInTheDocument();
});

test('zero_dte banner absent for future expiry', () => {
  render(
    <OptionDetailsSection
      row={mockRow}
      underlyingSymbol="SPY"
      expiryIso="2030-01-01"
      onSideChange={vi.fn()}
    />,
  );
  expect(screen.queryByTestId('zero-dte-banner')).not.toBeInTheDocument();
});
