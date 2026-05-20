import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrategyGenFeed } from '../StrategyGenFeed';
import * as api from '../../../services/strategy-gen/api';

vi.mock('../../../services/strategy-gen/api');

function wrapper({ children }: { children: React.ReactNode }): React.JSX.Element {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe('StrategyGenFeed', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading state', () => {
    vi.mocked(api.listStrategies).mockReturnValue(new Promise(vi.fn()));
    render(<StrategyGenFeed />, { wrapper });
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it('shows empty state when no strategies', async () => {
    vi.mocked(api.listStrategies).mockResolvedValue([]);
    render(<StrategyGenFeed />, { wrapper });
    expect(await screen.findByText(/no strategies/i)).toBeInTheDocument();
  });

  it('renders strategy rows', async () => {
    vi.mocked(api.listStrategies).mockResolvedValue([
      {
        id: 1,
        name: 'gen_stock_test',
        source_hash: 'abc123',
        llm_model: 'gpt-4',
        sandbox_status: 'validated',
        sandbox_error: null,
        backtest_id: null,
        approved_by: null,
        approved_at: null,
        created_at: '2026-05-20T10:00:00Z',
      },
    ]);
    render(<StrategyGenFeed />, { wrapper });
    expect(await screen.findByText('gen_stock_test')).toBeInTheDocument();
    expect(screen.getByText('validated')).toBeInTheDocument();
  });

  it('shows error state', async () => {
    vi.mocked(api.listStrategies).mockRejectedValue(new Error('network error'));
    render(<StrategyGenFeed />, { wrapper });
    expect(await screen.findByText(/failed/i)).toBeInTheDocument();
  });
});
