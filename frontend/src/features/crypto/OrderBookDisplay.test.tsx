import { expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { OrderBookDisplay } from './OrderBookDisplay';
import type { OrderBookSnapshot } from '@/services/crypto/types';

const snap: OrderBookSnapshot = {
  canonical_id: 'BTC.USD',
  bids: [{ price: '50000', qty: '0.5' }, { price: '49999', qty: '1.0' }],
  asks: [{ price: '50001', qty: '0.3' }],
  seq: 42,
};

test('renders bid and ask prices', () => {
  render(<OrderBookDisplay snapshot={snap} isStale={false} />);
  expect(screen.getByText('50000')).toBeInTheDocument();
  expect(screen.getByText('50001')).toBeInTheDocument();
});

test('shows stale badge when isStale', () => {
  render(<OrderBookDisplay snapshot={snap} isStale={true} />);
  expect(screen.getByText(/stale/i)).toBeInTheDocument();
});

test('shows spread', () => {
  render(<OrderBookDisplay snapshot={snap} isStale={false} />);
  expect(screen.getByText(/spread/i)).toBeInTheDocument();
});
