import { expect, test, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { OptionChainTable } from './OptionChainTable';
import type { OptionChainData } from './types';

function makeRow(strike: string, putCall: 'C' | 'P'): OptionChainRow {
  return {
    conid: `${putCall}${strike}`,
    strike,
    put_call: putCall,
    bid: '5.00',
    ask: '5.20',
    iv: 0.175,
    delta: putCall === 'C' ? 0.5 : -0.5,
    gamma: 0.028,
    theta: -0.12,
    vega: 0.31,
    open_interest: 38000,
    volume: 1200,
    multiplier: 100,
    exchange: 'CBOE',
    style: 'A',
  };
}

const mockData: OptionChainData = {
  calls: [makeRow('440', 'C'), makeRow('450', 'C'), makeRow('460', 'C')],
  puts: [makeRow('440', 'P'), makeRow('450', 'P'), makeRow('460', 'P')],
  source: 'ibkr',
  fetched_at_ms: 0,
};

test('renders ATM strike with star marker', () => {
  render(<OptionChainTable data={mockData} spot={450} onSelectStrike={vi.fn()} />);
  const atmRow = screen.getByTestId('chain-row-450');
  expect(atmRow).toBeInTheDocument();
  expect(screen.getAllByText(/450 ★/).length).toBeGreaterThan(0);
});

test('calls onSelectStrike when call bid cell clicked', () => {
  const handler = vi.fn();
  render(<OptionChainTable data={mockData} spot={450} onSelectStrike={handler} />);
  const row = screen.getByTestId('chain-row-450');
  const cells = Array.from(row.querySelectorAll('td'));
  const firstCell = cells[0];
  if (firstCell) fireEvent.click(firstCell);
  expect(handler).toHaveBeenCalledWith(
    expect.objectContaining({ strike: '450', put_call: 'C' }),
    'call',
  );
});

test('renders mobile collapse view below md', () => {
  render(<OptionChainTable data={mockData} spot={450} onSelectStrike={vi.fn()} />);
  expect(screen.getByTestId('chain-row-mobile-450')).toBeInTheDocument();
});
