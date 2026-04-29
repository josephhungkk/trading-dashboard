import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { FillsTable } from './FillsTable';
import type { FillResponse } from './FillsTable';

function makeFill(
  overrides: Partial<FillResponse> & { id: string; executed_at: string },
): FillResponse {
  return {
    commission: '1.50000000',
    commission_currency: 'USD',
    currency: 'USD',
    exec_id: `exec-${overrides.id}`,
    order_id: `00000000-0000-0000-0000-00000000000${overrides.id}`,
    price: '185.32000000',
    qty: '10.00000000',
    ...overrides,
  };
}

const mockFills: FillResponse[] = [
  makeFill({ id: '1', executed_at: '2026-04-28T09:31:00Z', price: '100.00000000', qty: '5.00000000' }),
  makeFill({ id: '2', executed_at: '2026-04-28T14:00:00Z', price: '200.00000000', qty: '3.00000000' }),
  makeFill({ id: '3', executed_at: '2026-04-29T10:00:00Z', price: '150.00000000', qty: '8.00000000' }),
];

describe('FillsTable', () => {
  it('renders all rows and column headers in order', () => {
    render(<FillsTable fills={mockFills} />);

    // Column headers present in order
    const headers = screen.getAllByRole('columnheader');
    const headerTexts = headers.map((h) => h.textContent?.trim());
    expect(headerTexts).toEqual([
      'Time (UTC)',
      'Symbol',
      'Side',
      'Qty',
      'Price',
      'Commission',
      'Total',
    ]);

    // All 3 fill rows render their truncated order id
    expect(screen.getAllByText('00000000…')).toHaveLength(3);

    // Table has aria-label
    expect(screen.getByRole('table', { name: 'Fills' })).toBeInTheDocument();
  });

  it('shows empty state message when fills is empty', () => {
    render(<FillsTable fills={[]} />);
    expect(screen.getByText('No fills in this date range')).toBeInTheDocument();
  });

  it('renders date group headers in chronological order for fills on 2 different dates', () => {
    render(<FillsTable fills={mockFills} />);

    const table = screen.getByRole('table', { name: 'Fills' });
    // Get all rowgroup headers (th scope="rowgroup")
    const groupHeaders = within(table).getAllByRole('rowheader');
    expect(groupHeaders).toHaveLength(2);

    // First group header should be for 2026-04-28
    expect(groupHeaders[0]?.textContent).toMatch(/28 Apr 2026/);
    // Second group header should be for 2026-04-29
    expect(groupHeaders[1]?.textContent).toMatch(/29 Apr 2026/);
  });
});
