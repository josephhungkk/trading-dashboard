import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';

import { renderWithQuery } from '../../../test-utils/render-with-query';
import type { ShadowComparisonReport, ShadowVsLive } from '@/services/shadow_promoter/types';

import { ShadowComparisonPanel } from './ShadowComparisonPanel';

const mocks = vi.hoisted(() => ({
  createShadow: vi.fn(),
  getShadowComparison: vi.fn(),
  promoteShadow: vi.fn(),
  mintCsrfNonce: vi.fn(),
  useShadowStream: vi.fn(),
}));

vi.mock('@/services/shadow_promoter/api', () => ({
  createShadow: mocks.createShadow,
  getShadowComparison: mocks.getShadowComparison,
  promoteShadow: mocks.promoteShadow,
}));

vi.mock('@/services/admin/api', () => ({
  mintCsrfNonce: mocks.mintCsrfNonce,
}));

vi.mock('../hooks/useShadowStream', () => ({
  useShadowStream: mocks.useShadowStream,
}));

function shadow(comparisonReady: boolean): ShadowVsLive {
  return {
    shadow_bot_id: 'shadow-1',
    shadow_metrics: {
      sharpe: 1.5,
      mar: 0.7,
      max_dd: -2.5,
      win_rate: 0.6,
      avg_trade_pnl: '15.00',
      total_trades: 12,
      window_days: 14,
    },
    live_metrics: {
      sharpe: 1.1,
      mar: 0.5,
      max_dd: -3.0,
      win_rate: 0.52,
      avg_trade_pnl: '11.00',
      total_trades: 10,
      window_days: 14,
    },
    delta: { sharpe: '+0.40', max_dd: '+0.50' },
    comparison_ready: comparisonReady,
  };
}

function report(shadows: ShadowVsLive[]): ShadowComparisonReport {
  return {
    live_bot_id: 'bot-1',
    shadows,
    generated_at: '2026-05-19T12:00:00Z',
  };
}

describe('ShadowComparisonPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getShadowComparison.mockResolvedValue(report([]));
    mocks.mintCsrfNonce.mockResolvedValue('nonce-1');
  });

  it('create form renders with default window 14', async () => {
    renderWithQuery(<ShadowComparisonPanel botId="bot-1" isAdmin />);
    expect(await screen.findByLabelText(/comparison window days/i)).toHaveValue(14);
  });

  it('not-ready badge shown when comparison_ready=false', async () => {
    mocks.getShadowComparison.mockResolvedValue(report([shadow(false)]));
    renderWithQuery(<ShadowComparisonPanel botId="bot-1" isAdmin />);
    expect(await screen.findByText('Not yet ready')).toBeInTheDocument();
  });

  it('promote button enabled when comparison_ready=true', async () => {
    mocks.getShadowComparison.mockResolvedValue(report([shadow(true)]));
    renderWithQuery(<ShadowComparisonPanel botId="bot-1" isAdmin />);
    expect(await screen.findByRole('button', { name: 'Promote' })).not.toBeDisabled();
  });

  it('promote button disabled when comparison_ready=false', async () => {
    mocks.getShadowComparison.mockResolvedValue(report([shadow(false)]));
    renderWithQuery(<ShadowComparisonPanel botId="bot-1" isAdmin />);
    expect(await screen.findByRole('button', { name: 'Promote' })).toBeDisabled();
  });
});
