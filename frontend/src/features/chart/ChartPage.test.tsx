import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import * as ReactQuery from '@tanstack/react-query';
import { ChartPage } from './ChartPage';

// Single top-level mock so Vitest hoisting works correctly.
// Individual tests override useQuery via vi.mocked(...).mockImplementation.
vi.mock('@tanstack/react-query', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-query')>();
  return { ...actual };
});

// Helper: wrap in a fresh QueryClient per test to avoid cross-test state
function renderWithQuery(ui: React.ReactElement): void {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe('ChartPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders title with canonical_id', () => {
    renderWithQuery(<ChartPage canonicalId="AAPL.US" />);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Chart — AAPL.US');
  });

  it('renders trade-chart placeholder', () => {
    renderWithQuery(<ChartPage canonicalId="AAPL.US" />);
    expect(screen.getByTestId('trade-chart')).toBeInTheDocument();
  });

  it('shows loading state during query', () => {
    vi.spyOn(ReactQuery, 'useQuery').mockReturnValue({
      isLoading: true,
      error: null,
      data: undefined,
    } as unknown as ReturnType<typeof ReactQuery.useQuery>);

    renderWithQuery(<ChartPage canonicalId="AAPL.US" />);
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('shows error state when query fails', () => {
    vi.spyOn(ReactQuery, 'useQuery').mockReturnValue({
      isLoading: false,
      error: new Error('boom'),
      data: undefined,
    } as unknown as ReturnType<typeof ReactQuery.useQuery>);

    renderWithQuery(<ChartPage canonicalId="AAPL.US" />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('chart_layouts query keyed by canonicalId', () => {
    const capturedKeys: unknown[] = [];
    vi.spyOn(ReactQuery, 'useQuery').mockImplementation(
      (opts: Parameters<typeof ReactQuery.useQuery>[0]) => {
        capturedKeys.push(opts.queryKey);
        return { isLoading: false, error: null, data: null } as unknown as ReturnType<
          typeof ReactQuery.useQuery
        >;
      },
    );

    renderWithQuery(<ChartPage canonicalId="AAPL.US" />);
    expect(capturedKeys[0]).toEqual(['chart-layouts', 'AAPL.US']);
  });
});
