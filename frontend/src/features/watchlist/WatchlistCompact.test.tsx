import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { WatchlistCompact } from './WatchlistCompact';
import { useModeStore } from '@/stores/global/mode';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';
import { fetchAccountsAndSyncMaintenance } from '@/hooks/useAccountsList';

describe('WatchlistCompact', () => {
  beforeEach(async () => {
    resetServices();
    const { live, paper } = getBothScopes();
    live.suspend();
    paper.suspend();
    useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
    await paper.hydrate(getServices(), fetchAccountsAndSyncMaintenance);
  });

  it('renders the active watchlist name as heading', async () => {
    render(<WatchlistCompact />);
    const heading = await screen.findByRole('heading', { level: 2 });
    expect(heading.textContent?.length ?? 0).toBeGreaterThan(0);
  });

  it('renders at most 10 symbol rows', async () => {
    const { container } = render(<WatchlistCompact />);
    await screen.findByRole('heading', { level: 2 });
    // NumericCell also uses font-mono + text-fg for neutral tone; narrow to the
    // symbol span (which has no tabular-nums).
    const symbolSpans = container.querySelectorAll('span.font-mono.text-fg:not(.tabular-nums)');
    expect(symbolSpans.length).toBeLessThanOrEqual(10);
    expect(symbolSpans.length).toBeGreaterThan(0);
  });

  it('falls back to "No active watchlist" when no watchlist is selected', async () => {
    const { paper } = getBothScopes();
    paper.suspend();
    render(<WatchlistCompact />);
    expect(await screen.findByText(/No active watchlist/i)).toBeInTheDocument();
  });
});
