import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('@/services/schwab', () => ({
  connectStart: vi.fn(),
  disconnect: vi.fn(),
  enableTier2: vi.fn(),
  getTokenStatus: vi.fn(),
  subscribeConfigStream: vi.fn(() => () => undefined),
}));

vi.mock('@/hooks/useSchwabTokenStatus', () => ({
  useSchwabTokenStatus: vi.fn(),
}));

import { SchwabCard } from './SchwabCard';
import { useSchwabTokenStatus } from '@/hooks/useSchwabTokenStatus';

const mockHook = useSchwabTokenStatus as unknown as ReturnType<typeof vi.fn>;

describe('SchwabCard', () => {
  it('shows Connected + countdown when fresh', () => {
    mockHook.mockReturnValue({
      status: {
        refreshTokenIssuedAt: new Date(Date.now() - 24 * 3_600_000),
        accessTokenIssuedAt: new Date(),
        tier2RefreshEnabled: false,
        tier2ConsecutiveFailures: 0,
      },
      loading: false,
      error: null,
      refetch: vi.fn(),
      startFastPoll: vi.fn(),
    });
    render(<SchwabCard />);
    expect(screen.getByText(/Connected/i)).toBeInTheDocument();
    expect(screen.getByText(/expires in/i)).toBeInTheDocument();
  });

  it('shows warn state when refresh_token age > 144h', () => {
    mockHook.mockReturnValue({
      status: {
        refreshTokenIssuedAt: new Date(Date.now() - 145 * 3_600_000),
        accessTokenIssuedAt: new Date(),
        tier2RefreshEnabled: false,
        tier2ConsecutiveFailures: 0,
      },
      loading: false,
      error: null,
      refetch: vi.fn(),
      startFastPoll: vi.fn(),
    });
    render(<SchwabCard />);
    const badge = screen.getByTestId('expiring-badge');
    expect(badge.dataset['state']).toBe('warn');
  });

  it('shows Not connected when no status', () => {
    mockHook.mockReturnValue({
      status: null,
      loading: false,
      error: null,
      refetch: vi.fn(),
      startFastPoll: vi.fn(),
    });
    render(<SchwabCard />);
    expect(screen.getByText(/Not connected/i)).toBeInTheDocument();
    expect(screen.getByText(/Connect Schwab/i)).toBeInTheDocument();
  });
});
