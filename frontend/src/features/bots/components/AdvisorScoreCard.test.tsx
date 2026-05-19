import type { ReactNode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AdvisorScoreCard } from './AdvisorScoreCard';

vi.mock('@/services/advisor/api', () => ({
  getAdvisorAttribution: vi.fn(),
}));

const mockSummary = {
  bot_id: '123e4567-e89b-12d3-a456-426614174000',
  window: '1h',
  veto_accuracy: 0.7,
  approve_accuracy: 0.6,
  avg_avoided_loss_quote: '450.00',
  avg_missed_gain_quote: null,
  complete_count: 10,
  partial_count: 2,
  pending_count: 5,
  bars_unavailable_count: 0,
  unresolvable_count: 0,
  skipped_count: 1,
  generated_at: '2026-05-19T14:00:00Z',
};

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe('AdvisorScoreCard', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('renders veto accuracy percentage', async () => {
    const { getAdvisorAttribution } = await import('@/services/advisor/api');
    vi.mocked(getAdvisorAttribution).mockResolvedValue(mockSummary);
    render(<AdvisorScoreCard botId="test-bot" advisorMode="VETO" />, { wrapper });
    await waitFor(() => expect(screen.getByText(/70%/)).toBeInTheDocument());
  });

  it('shows no-data message when complete_count is 0', async () => {
    const { getAdvisorAttribution } = await import('@/services/advisor/api');
    vi.mocked(getAdvisorAttribution).mockResolvedValue({ ...mockSummary, complete_count: 0 });
    render(<AdvisorScoreCard botId="test-bot" advisorMode="VETO" />, { wrapper });
    await waitFor(() =>
      expect(screen.getByText(/No attribution data yet/)).toBeInTheDocument(),
    );
  });

  it('returns null when advisorMode is OFF', () => {
    const { container } = render(
      <AdvisorScoreCard botId="test-bot" advisorMode="OFF" />,
      { wrapper },
    );
    expect(container).toBeEmptyDOMElement();
  });
});
