import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { FxTicketSection } from './FxTicketSection';
import * as api from '@/services/forex/api';
import type { FxPair, FxQuote } from '@/services/forex/types';

vi.mock('@/services/forex/api');

const mockPair: FxPair = {
  canonical_id: 'forex:EURUSD',
  base_currency: 'EUR',
  quote_currency: 'USD',
  pip_size: '0.0001',
};

const mockQuote: FxQuote = {
  id: 'abc123',
  broker_quote_id: 'bq-001',
  bid: '1.0820',
  ask: '1.0822',
  ttl_seconds: 10,
  expires_at: new Date(Date.now() + 30000).toISOString(),
  status: 'pending',
  side: null,
  notional: null,
  notional_currency: null,
  request_id: 'req-001',
};

describe('FxTicketSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders pair name', () => {
    render(<FxTicketSection accountId='acc-1' pair={mockPair} />);
    expect(screen.getByText('EUR/USD')).toBeInTheDocument();
  });

  it('calls requestQuote and shows FxQuoteDisplay', async () => {
    vi.mocked(api.requestQuote).mockResolvedValue(mockQuote);
    render(<FxTicketSection accountId='acc-1' pair={mockPair} />);
    fireEvent.change(screen.getByPlaceholderText('Notional'), { target: { value: '10000' } });
    fireEvent.click(screen.getByText('Get Quote'));
    await waitFor(() => expect(screen.getAllByText(/1\.0820/).length).toBeGreaterThan(0));
  });

  it('shows error when requestQuote fails', async () => {
    vi.mocked(api.requestQuote).mockRejectedValue(new Error('rate_limited'));
    render(<FxTicketSection accountId='acc-1' pair={mockPair} />);
    fireEvent.change(screen.getByPlaceholderText('Notional'), { target: { value: '100' } });
    fireEvent.click(screen.getByText('Get Quote'));
    await waitFor(() => expect(screen.getByText(/rate_limited/)).toBeInTheDocument());
  });
});
