import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QuoteFeedDropdown } from './QuoteFeedDropdown';
import { useQuoteFeedStore } from '@/stores/global/quote-feeds';
import type { QuoteFeedStatus } from '@/services/types';

function stubRadixPointer(): void {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  if (typeof proto['hasPointerCapture'] !== 'function') proto['hasPointerCapture'] = () => false;
  if (typeof proto['releasePointerCapture'] !== 'function') proto['releasePointerCapture'] = () => { /* jsdom stub */ };
  if (typeof proto['setPointerCapture'] !== 'function') proto['setPointerCapture'] = () => { /* jsdom stub */ };
  if (typeof proto['scrollIntoView'] !== 'function') proto['scrollIntoView'] = () => { /* jsdom stub */ };
}

const allRealtime: QuoteFeedStatus[] = [
  { assetClass: 'stock', exchange: 'NYSE',   feedType: 'realtime' },
  { assetClass: 'stock', exchange: 'NASDAQ', feedType: 'realtime' },
  { assetClass: 'forex',                     feedType: 'realtime' },
];

const someDelayed: QuoteFeedStatus[] = [
  { assetClass: 'stock', exchange: 'NYSE', feedType: 'realtime' },
  { assetClass: 'options',                 feedType: 'delayed' },
];

const oneOffline: QuoteFeedStatus[] = [
  { assetClass: 'stock',   exchange: 'NYSE', feedType: 'realtime' },
  { assetClass: 'futures', exchange: 'CME',  feedType: 'none' },
];

describe('QuoteFeedDropdown', () => {
  beforeEach(() => { stubRadixPointer(); });

  it('renders trigger labeled quote feed status', () => {
    useQuoteFeedStore.setState({ feeds: allRealtime });
    render(<QuoteFeedDropdown />);
    expect(screen.getByRole('button', { name: /quote feed status/i })).toBeInTheDocument();
  });

  it('trigger shows Realtime when all feeds are realtime', () => {
    useQuoteFeedStore.setState({ feeds: allRealtime });
    render(<QuoteFeedDropdown />);
    expect(screen.getAllByText(/Realtime/i).length).toBeGreaterThan(0);
  });

  it('trigger shows Delayed when any feed is delayed', () => {
    useQuoteFeedStore.setState({ feeds: someDelayed });
    render(<QuoteFeedDropdown />);
    expect(screen.getAllByText(/Delayed/i).length).toBeGreaterThan(0);
  });

  it('trigger shows Offline when any feed is none', () => {
    useQuoteFeedStore.setState({ feeds: oneOffline });
    render(<QuoteFeedDropdown />);
    expect(screen.getAllByText(/Offline/i).length).toBeGreaterThan(0);
  });

  it('opens menu on click and groups rows by asset class', async () => {
    const user = userEvent.setup();
    useQuoteFeedStore.setState({ feeds: someDelayed });
    render(<QuoteFeedDropdown />);
    await user.click(screen.getByRole('button', { name: /quote feed status/i }));
    // 2 groups (stock, options) -> 2 rows since each has 1 entry.
    const items = screen.getAllByRole('menuitem');
    expect(items.length).toBe(2);
  });
});
