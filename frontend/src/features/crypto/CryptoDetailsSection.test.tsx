import { expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CryptoDetailsSection } from './CryptoDetailsSection';
import type { CryptoAsset } from '@/services/crypto/types';

const asset: CryptoAsset = {
  canonical_id: 'BTC.USD',
  base_asset: 'BTC',
  quote_asset: 'USD',
  min_qty: '0.00001',
  qty_step: '0.00001',
  min_notional: '10',
  available_24h: true,
};

test('renders pair name', () => {
  render(<CryptoDetailsSection asset={asset} />);
  expect(screen.getByText('BTC/USD')).toBeInTheDocument();
});

test('renders min qty', () => {
  render(<CryptoDetailsSection asset={asset} />);
  expect(screen.getByText('0.00001')).toBeInTheDocument();
});
